from __future__ import annotations

import logging
import os
import time
import warnings
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional, Union, overload

import numpy as np
import xarray as xr
from typing_extensions import Self

from ert.field_utils import FieldFileFormat, Shape, get_mask, read_field, save_field

from ._option_dict import option_dict
from ._str_to_bool import str_to_bool
from .parameter_config import ParameterConfig
from .parsing import ConfigValidationError, ConfigWarning

if TYPE_CHECKING:
    import numpy.typing as npt

    from ert.storage import EnsembleReader

_logger = logging.getLogger(__name__)


@dataclass
class Field(ParameterConfig):  # pylint: disable=too-many-instance-attributes
    nx: int
    ny: int
    nz: int
    file_format: FieldFileFormat
    output_transformation: Optional[str]
    input_transformation: Optional[str]
    truncation_min: Optional[float]
    truncation_max: Optional[float]
    forward_init_file: str
    output_file: Path
    grid_file: str
    mask_file: Optional[Path] = None

    @classmethod
    def from_config_list(
        cls,
        grid_file_path: str,
        dims: Shape,
        config_list: List[str],
    ) -> Self:
        name = config_list[0]
        out_file = Path(config_list[2])
        options = option_dict(config_list, 3)
        init_transform = options.get("INIT_TRANSFORM")
        forward_init = str_to_bool(options.get("FORWARD_INIT", "FALSE"))
        output_transform = options.get("OUTPUT_TRANSFORM")
        input_transform = options.get("INPUT_TRANSFORM")
        min_ = options.get("MIN")
        max_ = options.get("MAX")
        init_files = options.get("INIT_FILES")
        if input_transform:
            warnings.warn(
                ConfigWarning.with_context(
                    f"Got INPUT_TRANSFORM for FIELD: {name}, "
                    f"this has no effect and can be removed",
                    config_list,
                ),
                stacklevel=1,
            )

        errors = []

        if init_transform and init_transform not in TRANSFORM_FUNCTIONS:
            errors.append(
                ConfigValidationError.with_context(
                    f"FIELD INIT_TRANSFORM:{init_transform} is an invalid function",
                    config_list,
                )
            )
        if output_transform and output_transform not in TRANSFORM_FUNCTIONS:
            errors.append(
                ConfigValidationError.with_context(
                    f"FIELD OUTPUT_TRANSFORM:{output_transform} is an invalid function",
                    config_list,
                )
            )
        file_extension = out_file.suffix[1:].upper()
        if out_file.suffix == "":
            errors.append(
                ConfigValidationError.with_context(
                    f"Missing extension for field output file '{out_file}', "
                    f"valid formats are: {[f.name for f in FieldFileFormat]}",
                    config_list[2],
                )
            )
        file_format = None
        try:
            file_format = FieldFileFormat[file_extension]
        except KeyError:
            errors.append(
                ConfigValidationError.with_context(
                    f"Unknown file format for output file: {out_file.suffix!r},"
                    f" valid formats: {[f.name for f in FieldFileFormat]}",
                    config_list[2],
                )
            )
        if init_files is None:
            errors.append(
                ConfigValidationError.with_context(
                    f"Missing required INIT_FILES for field {name!r}", config_list
                )
            )

        if errors:
            raise ConfigValidationError.from_collected(errors)
        assert file_format is not None

        assert init_files is not None
        return cls(
            name=name,
            nx=dims.nx,
            ny=dims.ny,
            nz=dims.nz,
            file_format=file_format,
            output_transformation=output_transform,
            input_transformation=init_transform,
            truncation_max=float(max_) if max_ is not None else None,
            truncation_min=float(min_) if min_ is not None else None,
            forward_init=forward_init,
            forward_init_file=init_files,
            output_file=out_file,
            grid_file=os.path.abspath(grid_file_path),
        )

    @property
    def size(self) -> int:
        return self.nx * self.ny * self.nz

    def read_from_runpath(self, run_path: Path, real_nr: int) -> xr.Dataset:
        t = time.perf_counter()
        file_name = self.forward_init_file
        if "%d" in file_name:
            file_name = file_name % real_nr  # noqa
        ds = xr.Dataset(
            {
                "values": (
                    ["x", "y", "z"],
                    field_transform(
                        read_field(
                            run_path / file_name,
                            self.name,
                            self.mask,
                            Shape(self.nx, self.ny, self.nz),
                        ),
                        self.input_transformation,
                    ),
                )
            }
        )
        _logger.debug(f"load() time_used {(time.perf_counter() - t):.4f}s")
        return ds

    def write_to_runpath(
        self, run_path: Path, real_nr: int, ensemble: EnsembleReader
    ) -> None:
        t = time.perf_counter()
        file_out = run_path.joinpath(self.output_file)
        if os.path.islink(file_out):
            os.unlink(file_out)

        save_field(
            self._transform_data(self._fetch_from_ensemble(real_nr, ensemble)),
            self.name,
            file_out,
            self.file_format,
        )

        _logger.debug(f"save() time_used {(time.perf_counter() - t):.4f}s")

    def _fetch_from_ensemble(
        self, real_nr: int, ensemble: EnsembleReader
    ) -> xr.DataArray:
        return ensemble.load_parameters(self.name, real_nr)

    def _transform_data(
        self, data_array: xr.DataArray
    ) -> np.ma.MaskedArray[Any, np.dtype[np.float32]]:
        return np.ma.MaskedArray(  # type: ignore
            _field_truncate(
                field_transform(
                    data_array,
                    transform_name=self.output_transformation,
                ),
                self.truncation_min,
                self.truncation_max,
            ),
            self.mask,
            fill_value=np.nan,
        )

    def save_experiment_data(self, experiment_path: Path) -> None:
        mask_path = experiment_path / "grid_mask.npy"
        if not mask_path.exists():
            mask, _ = get_mask(self.grid_file)
            np.save(mask_path, mask)
        self.mask_file = mask_path

    @cached_property
    def mask(self) -> Any:
        if self.mask_file is None:
            raise ValueError(
                "In order to get Field.mask, Field.save_experiment_data has"
                " to be called first"
            )
        return np.load(self.mask_file)


TRANSFORM_FUNCTIONS = {
    "LN": np.log,
    "LOG": np.log,
    "LN0": lambda v: np.log(v + 0.000001),
    "LOG10": np.log10,
    "EXP": np.exp,
    "EXP0": lambda v: np.exp(v) - 0.000001,
    "POW10": lambda v: np.power(10.0, v),
    "TRUNC_POW10": lambda v: np.maximum(np.power(10, v), 0.001),
}


@overload
def field_transform(
    data: xr.DataArray, transform_name: Optional[str]
) -> Union[npt.NDArray[np.float32], xr.DataArray]:
    pass


@overload
def field_transform(
    data: npt.NDArray[np.float32], transform_name: Optional[str]
) -> npt.NDArray[np.float32]:
    pass


def field_transform(
    data: Union[xr.DataArray, npt.NDArray[np.float32]], transform_name: Optional[str]
) -> Union[npt.NDArray[np.float32], xr.DataArray]:
    if transform_name is None:
        return data
    return TRANSFORM_FUNCTIONS[transform_name](data)  # type: ignore


def _field_truncate(
    data: npt.ArrayLike, min_: Optional[float], max_: Optional[float]
) -> Any:
    if min_ is not None and max_ is not None:
        vfunc = np.vectorize(lambda x: max(min(x, max_), min_))
        return vfunc(data)
    elif min_ is not None:
        vfunc = np.vectorize(lambda x: max(x, min_))
        return vfunc(data)
    elif max_ is not None:
        vfunc = np.vectorize(lambda x: min(x, max_))
        return vfunc(data)
    return data
