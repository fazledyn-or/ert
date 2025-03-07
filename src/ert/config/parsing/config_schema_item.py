import os
import shutil
from typing import List, Mapping, Optional, TypeVar, Union

from pydantic import BaseModel, NonNegativeInt, PositiveInt

from .config_errors import ConfigValidationError
from .context_values import (
    ContextBool,
    ContextFloat,
    ContextInt,
    ContextList,
    ContextString,
    ContextValue,
)
from .deprecation_info import DeprecationInfo
from .error_info import ErrorInfo
from .file_context_token import FileContextToken
from .schema_item_type import SchemaItemType

T = TypeVar("T")


class SchemaItem(BaseModel):
    # The kw which identifies this item
    kw: str

    # The minimum number of arguments
    argc_min: NonNegativeInt = 1
    # The maximum number of arguments: None means no upper limit
    argc_max: Optional[NonNegativeInt] = 1
    # A list of types for the items. Set along with argc_minmax()
    type_map: List[Optional[SchemaItemType]] = []
    # A list of item's which must also be set (if this item is set). (can be NULL)
    required_children: List[str] = []
    # Information about the deprecation if deprecated
    deprecation_info: Optional[DeprecationInfo] = None
    # if positive, arguments after this count will be concatenated with a " " between
    join_after: Optional[PositiveInt] = None
    # if true, will accumulate many values set for key, otherwise each entry will
    # overwrite any previous value set
    multi_occurrence: bool = False
    expand_envvar: bool = True
    # Index of tokens to do substitution from until end
    substitute_from: NonNegativeInt = 1
    required_set: bool = False
    required_children_value: Mapping[str, List[str]] = {}
    # Allowed values for arguments, if empty, all values allowed
    common_selection_set: List[str] = []
    # Allowed values for specific arguments, if no entry, all values allowed
    indexed_selection_set: Mapping[int, List[str]] = {}

    def _is_in_allowed_values_for_arg_at_index(
        self, token: "FileContextToken", index: int
    ) -> bool:
        return not (
            index in self.indexed_selection_set
            and token not in self.indexed_selection_set[index]
        )

    @classmethod
    def deprecated_dummy_keyword(cls, info: DeprecationInfo) -> "SchemaItem":
        return SchemaItem(
            kw=info.keyword,
            deprecation_info=info,
            required_set=False,
            argc_min=0,
            argc_max=None,
        )

    def token_to_value_with_context(
        self, token: FileContextToken, index: int, keyword: FileContextToken, cwd: str
    ) -> Optional[ContextValue]:
        """
        Converts a FileContextToken to a value with context that
        behaves like a value, but also contains its location in the file,
        as well the keyword it pertains to and its location in the file.

        :param token: the token to be converted
        :param index: the index of the token
        :param keyword: the keyword it pertains to
        :param cwd: the current working directory of the file being parsed

        :return: The token as a value with context of itself and its keyword
        """
        # pylint: disable=too-many-return-statements, too-many-branches

        if not self._is_in_allowed_values_for_arg_at_index(token, index):
            raise ConfigValidationError.with_context(
                (
                    f"{self.kw!r} argument {index + 1!r} must be one of"
                    f" {self.indexed_selection_set[index]!r} was {token.value!r}"
                ),
                token,
            )

        if not len(self.type_map) > index:
            return ContextString(str(token), token, keyword)
        val_type = self.type_map[index]
        if val_type is None:
            return ContextString(str(token), token, keyword)
        if val_type == SchemaItemType.BOOL:
            if token.lower() == "true":
                return ContextBool(True, token, keyword)
            elif token.lower() == "false":
                return ContextBool(False, token, keyword)
            else:
                raise ConfigValidationError.with_context(
                    f"{self.kw!r} must have a boolean value as argument {index + 1!r}",
                    token,
                )
        if val_type == SchemaItemType.INT:
            try:
                return ContextInt(int(token), token, keyword)
            except ValueError as e:
                raise ConfigValidationError.with_context(
                    f"{self.kw!r} must have an integer value as argument {index + 1!r}",
                    token,
                ) from e
        if val_type == SchemaItemType.FLOAT:
            try:
                return ContextFloat(float(token), token, keyword)
            except ValueError as e:
                raise ConfigValidationError.with_context(
                    f"{self.kw!r} must have a number as argument {index + 1!r}", token
                ) from e

        path: Optional[str] = str(token)
        if val_type in [
            SchemaItemType.PATH,
            SchemaItemType.EXISTING_PATH,
        ]:
            if not os.path.isabs(token):
                path = os.path.normpath(
                    os.path.join(os.path.dirname(token.filename), token)
                )
            if val_type == SchemaItemType.EXISTING_PATH and not os.path.exists(
                str(path)
            ):
                err = f'Cannot find file or directory "{token.value}". '
                if path != token:
                    err += f"The configured value was {path!r} "
                raise ConfigValidationError.with_context(err, token)

            assert isinstance(path, str)
            return ContextString(path, token, keyword)
        if val_type == SchemaItemType.EXECUTABLE:
            absolute_path: Optional[str]
            if not os.path.isabs(token):
                # Try relative
                absolute_path = os.path.abspath(os.path.join(cwd, token))
            else:
                absolute_path = token
            if not os.path.exists(absolute_path):
                absolute_path = shutil.which(token)

            if absolute_path is None:
                raise ConfigValidationError.with_context(
                    f"Could not find executable {token.value!r}", token
                )

            if os.path.isdir(absolute_path):
                raise ConfigValidationError.with_context(
                    f"Expected executable file, but {token.value!r} is a directory.",
                    token,
                )

            if not os.access(absolute_path, os.X_OK):
                context = (
                    f"{token.value!r} which was resolved to {absolute_path!r}"
                    if token.value != absolute_path
                    else f"{token.value!r}"
                )
                raise ConfigValidationError.with_context(
                    f"File not executable: {context}", token
                )
            return ContextString(absolute_path, token, keyword)
        return ContextString(str(token), token, keyword)

    def apply_constraints(
        self,
        args: List[T],
        keyword: FileContextToken,
        cwd: str,
    ) -> Union[T, ContextValue, None, ContextList[Union[T, ContextValue, None]]]:
        errors: List[Union[ErrorInfo, ConfigValidationError]] = []

        args_with_context: ContextList[Union[T, ContextValue, None]] = ContextList(
            token=keyword
        )
        for i, x in enumerate(args):
            if isinstance(x, FileContextToken):
                try:
                    value_with_context = self.token_to_value_with_context(
                        x, i, keyword, cwd
                    )
                    args_with_context.append(value_with_context)
                except ConfigValidationError as err:
                    errors.append(err)
                    continue
            else:
                args_with_context.append(x)

        if len(args) < self.argc_min:
            errors.append(
                ErrorInfo(
                    message=f"{self.kw} must have at least {self.argc_min} arguments",
                    filename=keyword.filename,
                ).set_context(ContextString.from_token(keyword))
            )
        elif self.argc_max is not None and len(args) > self.argc_max:
            errors.append(
                ErrorInfo(
                    f"{self.kw} must have maximum {self.argc_max} arguments",
                ).set_context(ContextString.from_token(keyword))
            )

        if len(errors) > 0:
            raise ConfigValidationError.from_collected(errors)

        if self.argc_max == 1 and self.argc_min == 1:
            return args_with_context[0]

        return args_with_context

    def join_args(self, line: List[FileContextToken]) -> List[FileContextToken]:
        n = self.join_after
        if n is not None and n < len(line):
            joined = FileContextToken.join_tokens(line[n:], " ")
            new_line = line[0:n]
            if len(joined) > 0:
                new_line.append(joined)
            return new_line
        return line


def float_keyword(keyword: str) -> SchemaItem:
    return SchemaItem(kw=keyword, type_map=[SchemaItemType.FLOAT])


def int_keyword(keyword: str) -> SchemaItem:
    return SchemaItem(kw=keyword, type_map=[SchemaItemType.INT])


def string_keyword(keyword: str) -> SchemaItem:
    return SchemaItem(kw=keyword, type_map=[SchemaItemType.STRING])


def path_keyword(keyword: str) -> SchemaItem:
    return SchemaItem(kw=keyword, type_map=[SchemaItemType.PATH])


def existing_path_keyword(keyword: str) -> SchemaItem:
    return SchemaItem(kw=keyword, type_map=[SchemaItemType.EXISTING_PATH])


def single_arg_keyword(keyword: str) -> SchemaItem:
    return SchemaItem(kw=keyword, argc_max=1, argc_min=1)
