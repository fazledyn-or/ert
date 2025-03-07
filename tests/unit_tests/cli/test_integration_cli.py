import fileinput
import json
import logging
import os
import shutil
import threading
from argparse import ArgumentParser
from pathlib import Path
from textwrap import dedent
from unittest.mock import Mock, call

import numpy as np
import pandas as pd
import pytest
import xtgeo

import ert.shared
from ert import LibresFacade, ensemble_evaluator
from ert.__main__ import ert_parser
from ert.cli import (
    ENSEMBLE_EXPERIMENT_MODE,
    ENSEMBLE_SMOOTHER_MODE,
    ES_MDA_MODE,
    ITERATIVE_ENSEMBLE_SMOOTHER_MODE,
    TEST_RUN_MODE,
)
from ert.cli.main import ErtCliError, run_cli
from ert.config import ConfigValidationError, ConfigWarning, ErtConfig
from ert.enkf_main import EnKFMain
from ert.shared.feature_toggling import FeatureToggling
from ert.storage import open_storage


@pytest.fixture(name="mock_cli_run")
def fixture_mock_cli_run(monkeypatch):
    mocked_monitor = Mock()
    mocked_thread_start = Mock()
    mocked_thread_join = Mock()
    monkeypatch.setattr(threading.Thread, "start", mocked_thread_start)
    monkeypatch.setattr(threading.Thread, "join", mocked_thread_join)
    monkeypatch.setattr(ert.cli.monitor.Monitor, "monitor", mocked_monitor)
    yield mocked_monitor, mocked_thread_join, mocked_thread_start


@pytest.mark.integration_test
def test_runpath_file(tmpdir, source_root):
    shutil.copytree(
        os.path.join(source_root, "test-data", "poly_example"),
        os.path.join(str(tmpdir), "poly_example"),
    )

    with tmpdir.as_cwd():
        with open("poly_example/poly.ert", "a", encoding="utf-8") as fh:
            config_lines = [
                "LOAD_WORKFLOW_JOB ASSERT_RUNPATH_FILE\n"
                "LOAD_WORKFLOW TEST_RUNPATH_FILE\n",
                "HOOK_WORKFLOW TEST_RUNPATH_FILE PRE_SIMULATION\n",
            ]

            fh.writelines(config_lines)

        parser = ArgumentParser(prog="test_main")
        parsed = ert_parser(
            parser,
            [
                ENSEMBLE_SMOOTHER_MODE,
                "--target-case",
                "poly_runpath_file",
                "--realizations",
                "1,2,4,8,16,32,64",
                "poly_example/poly.ert",
                "--port-range",
                "1024-65535",
            ],
        )

        run_cli(parsed)

        assert os.path.isfile("RUNPATH_WORKFLOW_0.OK")
        assert os.path.isfile("RUNPATH_WORKFLOW_1.OK")


@pytest.mark.integration_test
def test_ensemble_evaluator(tmpdir, source_root):
    shutil.copytree(
        os.path.join(source_root, "test-data", "poly_example"),
        os.path.join(str(tmpdir), "poly_example"),
    )

    with tmpdir.as_cwd():
        parser = ArgumentParser(prog="test_main")
        parsed = ert_parser(
            parser,
            [
                ENSEMBLE_SMOOTHER_MODE,
                "--target-case",
                "poly_runpath_file",
                "--realizations",
                "1,2,4,8,16,32,64",
                "poly_example/poly.ert",
                "--port-range",
                "1024-65535",
            ],
        )
        FeatureToggling.update_from_args(parsed)

        run_cli(parsed)
        FeatureToggling.reset()


@pytest.mark.integration_test
def test_es_mda(tmpdir, source_root, snapshot):
    shutil.copytree(
        os.path.join(source_root, "test-data", "poly_example"),
        os.path.join(str(tmpdir), "poly_example"),
    )

    with tmpdir.as_cwd():
        with fileinput.input("poly_example/poly.ert", inplace=True) as fin:
            for line_nr, line in enumerate(fin):
                if line_nr == 1:
                    print("RANDOM_SEED 1234", end="")
                print(line, end="")
        parser = ArgumentParser(prog="test_main")
        parsed = ert_parser(
            parser,
            [
                ES_MDA_MODE,
                "--target-case",
                "iter-%d",
                "--realizations",
                "1,2,4,8,16",
                "poly_example/poly.ert",
                "--port-range",
                "1024-65535",
            ],
        )
        FeatureToggling.update_from_args(parsed)

        run_cli(parsed)
        FeatureToggling.reset()
        facade = LibresFacade.from_config_file("poly.ert")
        with open_storage("storage", "r") as storage:
            data = []
            for iter_nr in range(4):
                data.append(
                    facade.load_all_gen_kw_data(
                        storage.get_ensemble_by_name(f"iter-{iter_nr}")
                    )
                )
        result = pd.concat(
            data,
            keys=[f"iter-{iter}" for iter in range(len(data))],
            names=("Iteration", "Realization"),
        )
        snapshot.assert_match(
            result.to_csv(float_format="%.12g"), "es_mda_integration_snapshot"
        )


@pytest.mark.parametrize(
    "mode, target",
    [
        pytest.param(ENSEMBLE_SMOOTHER_MODE, "target", id=f"{ENSEMBLE_SMOOTHER_MODE}"),
        pytest.param(
            ITERATIVE_ENSEMBLE_SMOOTHER_MODE,
            "iter-%d",
            id=f"{ITERATIVE_ENSEMBLE_SMOOTHER_MODE}",
        ),
        pytest.param(ES_MDA_MODE, "iter-%d", id=f"{ES_MDA_MODE}"),
    ],
)
@pytest.mark.integration_test
def test_cli_does_not_run_without_observations(tmpdir, source_root, mode, target):
    shutil.copytree(
        os.path.join(source_root, "test-data", "poly_example"),
        os.path.join(str(tmpdir), "poly_example"),
    )

    def remove_line(file_name, line_num):
        with open(file_name, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(file_name, "w", encoding="utf-8") as f:
            f.writelines(lines[: line_num - 1] + lines[line_num:])

    with tmpdir.as_cwd():
        # Remove observations from config file
        remove_line("poly_example/poly.ert", 8)

        parser = ArgumentParser(prog="test_main")
        parsed = ert_parser(
            parser,
            [
                mode,
                "--target-case",
                target,
                "poly_example/poly.ert",
            ],
        )
        with pytest.raises(
            ErtCliError, match=f"To run {mode}, observations are needed."
        ):
            run_cli(parsed)


@pytest.mark.integration_test
def test_ensemble_evaluator_disable_monitoring(tmpdir, source_root):
    shutil.copytree(
        os.path.join(source_root, "test-data", "poly_example"),
        os.path.join(str(tmpdir), "poly_example"),
    )

    with tmpdir.as_cwd():
        parser = ArgumentParser(prog="test_main")
        parsed = ert_parser(
            parser,
            [
                ENSEMBLE_SMOOTHER_MODE,
                "--disable-monitoring",
                "--target-case",
                "poly_runpath_file",
                "--realizations",
                "1,2,4,8,16,32,64",
                "poly_example/poly.ert",
                "--port-range",
                "1024-65535",
            ],
        )
        FeatureToggling.update_from_args(parsed)

        run_cli(parsed)
        FeatureToggling.reset()


@pytest.mark.integration_test
def test_cli_test_run(tmpdir, source_root, mock_cli_run):
    shutil.copytree(
        os.path.join(source_root, "test-data", "poly_example"),
        os.path.join(str(tmpdir), "poly_example"),
    )

    with tmpdir.as_cwd():
        parser = ArgumentParser(prog="test_main")
        parsed = ert_parser(
            parser,
            [
                TEST_RUN_MODE,
                "poly_example/poly.ert",
                "--port-range",
                "1024-65535",
            ],
        )
        run_cli(parsed)

    monitor_mock, thread_join_mock, thread_start_mock = mock_cli_run
    monitor_mock.assert_called_once()
    thread_join_mock.assert_called_once()
    thread_start_mock.assert_has_calls([[call(), call()]])


@pytest.mark.integration_test
def test_ies(tmpdir, source_root):
    shutil.copytree(
        os.path.join(source_root, "test-data", "poly_example"),
        os.path.join(str(tmpdir), "poly_example"),
    )

    with tmpdir.as_cwd():
        parser = ArgumentParser(prog="test_main")
        parsed = ert_parser(
            parser,
            [
                ITERATIVE_ENSEMBLE_SMOOTHER_MODE,
                "--target-case",
                "iter-%d",
                "--realizations",
                "1,2,4,8,16",
                "poly_example/poly.ert",
                "--port-range",
                "1024-65535",
            ],
        )
        FeatureToggling.update_from_args(parsed)

        run_cli(parsed)
        FeatureToggling.reset()


@pytest.mark.integration_test
def test_that_running_ies_with_different_steplength_produces_different_result(
    tmpdir, source_root
):
    """This is a regression test to make sure that different step-lengths
    give different results when running SIES.
    """
    shutil.copytree(
        os.path.join(source_root, "test-data", "poly_example"),
        os.path.join(str(tmpdir), "poly_example"),
    )

    def _run(target):
        parser = ArgumentParser(prog="test_main")
        parsed = ert_parser(
            parser,
            [
                ITERATIVE_ENSEMBLE_SMOOTHER_MODE,
                "--target-case",
                f"{target}-%d",
                "--realizations",
                "1,2,4,8",
                "poly_example/poly.ert",
                "--num-iterations",
                "1",
            ],
        )
        run_cli(parsed)
        facade = LibresFacade.from_config_file("poly.ert")

        with open_storage(facade.enspath) as storage:
            iter_0_fs = storage.get_ensemble_by_name(f"{target}-0")
            df_iter_0 = facade.load_all_gen_kw_data(iter_0_fs)
            iter_1_fs = storage.get_ensemble_by_name(f"{target}-1")
            df_iter_1 = facade.load_all_gen_kw_data(iter_1_fs)

            result = pd.concat(
                [df_iter_0, df_iter_1],
                keys=["iter-0", "iter-1"],
            )
            return result

    # Run SIES with step-lengths defined
    with tmpdir.as_cwd():
        with open("poly_example/poly.ert", mode="a", encoding="utf-8") as fh:
            fh.write(
                dedent(
                    """
                RANDOM_SEED 123456
                ANALYSIS_SELECT IES_ENKF
                ANALYSIS_SET_VAR IES_ENKF IES_MAX_STEPLENGTH 0.5
                ANALYSIS_SET_VAR IES_ENKF IES_MIN_STEPLENGTH 0.2
                ANALYSIS_SET_VAR IES_ENKF IES_DEC_STEPLENGTH 2.5
                """
                )
            )

        result_1 = _run("target_result_1")

    # Run SIES with different step-lengths defined
    with tmpdir.as_cwd():
        with open("poly_example/poly.ert", mode="a", encoding="utf-8") as fh:
            fh.write(
                dedent(
                    """
                ANALYSIS_SELECT IES_ENKF
                ANALYSIS_SET_VAR IES_ENKF IES_MAX_STEPLENGTH 0.6
                ANALYSIS_SET_VAR IES_ENKF IES_MIN_STEPLENGTH 0.3
                ANALYSIS_SET_VAR IES_ENKF IES_DEC_STEPLENGTH 2.0
                """
                )
            )

        result_2 = _run("target_result_2")

        # Prior should be the same
        assert result_1.loc["iter-0"].equals(result_2.loc["iter-0"])

        # Posterior should be different
        assert not np.isclose(result_1.loc["iter-1"], result_2.loc["iter-1"]).all()


@pytest.mark.filterwarnings("ignore::ert.config.ConfigWarning")
def test_bad_config_error_message(tmp_path):
    (tmp_path / "test.ert").write_text("NUM_REL 10\n")
    parser = ArgumentParser(prog="test_main")
    parsed = ert_parser(
        parser,
        [
            TEST_RUN_MODE,
            str(tmp_path / "test.ert"),
        ],
    )
    with pytest.raises(ConfigValidationError, match="NUM_REALIZATIONS must be set."):
        run_cli(parsed)


@pytest.mark.integration_test
@pytest.mark.parametrize(
    "prior_mask,reals_rerun_option,should_resample",
    [
        pytest.param(
            None, "0-4", False, id="All realisations first, subset second run"
        ),
        pytest.param(
            [False, True, True, True, True],
            "2-3",
            False,
            id="Subset of realisation first run, subs-subset second run",
        ),
        pytest.param(
            [True] * 3,
            "0-5",
            True,
            id="Subset of realisation first, superset in second run - must resample",
        ),
    ],
)
def test_that_prior_is_not_overwritten_in_ensemble_experiment(
    prior_mask,
    reals_rerun_option,
    should_resample,
    tmpdir,
    source_root,
    capsys,
):
    # pylint: disable=too-many-arguments
    shutil.copytree(
        os.path.join(source_root, "test-data", "poly_example"),
        os.path.join(str(tmpdir), "poly_example"),
    )

    with tmpdir.as_cwd():
        test_ert = EnKFMain(ErtConfig.from_file("poly_example/poly.ert"))
        prior_mask = prior_mask or [True] * test_ert.getEnsembleSize()
        storage = open_storage(test_ert.ert_config.ens_path, mode="w")
        experiment_id = storage.create_experiment(
            test_ert.ensembleConfig().parameter_configuration
        )
        ensemble = storage.create_ensemble(
            experiment_id, name="iter-0", ensemble_size=test_ert.getEnsembleSize()
        )
        prior_ensemble_id = ensemble.id
        prior_context = test_ert.ensemble_context(ensemble, prior_mask, 0)
        test_ert.sample_prior(prior_context.sim_fs, prior_context.active_realizations)
        facade = LibresFacade(test_ert)
        prior_values = facade.load_all_gen_kw_data(
            storage.get_ensemble_by_name("iter-0")
        )
        storage.close()

        parser = ArgumentParser(prog="test_main")
        parsed = ert_parser(
            parser,
            [
                ENSEMBLE_EXPERIMENT_MODE,
                "poly_example/poly.ert",
                "--current-case=iter-0",
                "--port-range",
                "1024-65535",
                "--realizations",
                reals_rerun_option,
            ],
        )

        FeatureToggling.update_from_args(parsed)
        run_cli(parsed)
        post_facade = LibresFacade.from_config_file("poly.ert")
        storage = open_storage(test_ert.ert_config.ens_path, mode="w")
        parameter_values = post_facade.load_all_gen_kw_data(
            storage.get_ensemble(prior_ensemble_id)
        )

        if should_resample:
            with pytest.raises(AssertionError):
                pd.testing.assert_frame_equal(parameter_values, prior_values)
        else:
            pd.testing.assert_frame_equal(parameter_values, prior_values)
        storage.close()


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(ENSEMBLE_SMOOTHER_MODE),
        pytest.param(ITERATIVE_ENSEMBLE_SMOOTHER_MODE),
        pytest.param(ES_MDA_MODE),
    ],
)
@pytest.mark.usefixtures("copy_poly_case")
def test_that_the_cli_raises_exceptions_when_parameters_are_missing(mode):
    with open("poly.ert", "r", encoding="utf-8") as fin, open(
        "poly-no-gen-kw.ert", "w", encoding="utf-8"
    ) as fout:
        for line in fin:
            if "GEN_KW" not in line:
                fout.write(line)

    args = Mock()
    args.config = "poly-no-gen-kw.ert"
    parser = ArgumentParser(prog="test_main")

    ert_args = [
        mode,
        "poly-no-gen-kw.ert",
        "--port-range",
        "1024-65535",
        "--target-case",
    ]

    testcase = "testcase" if mode is ENSEMBLE_SMOOTHER_MODE else "testcase-%d"
    ert_args.append(testcase)

    parsed = ert_parser(
        parser,
        ert_args,
    )

    with pytest.raises(
        ErtCliError,
        match=f"To run {mode}, GEN_KW, FIELD or SURFACE parameters are needed.",
    ):
        run_cli(parsed)


@pytest.mark.usefixtures("copy_poly_case")
def test_that_the_cli_raises_exceptions_when_no_weight_provided_for_es_mda():
    args = Mock()
    args.config = "poly.ert"
    parser = ArgumentParser(prog="test_main")

    ert_args = [
        "es_mda",
        "poly.ert",
        "--port-range",
        "1024-65535",
        "--target-case",
        "testcase-%d",
        "--weights",
        "0",
    ]

    parsed = ert_parser(
        parser,
        ert_args,
    )

    with pytest.raises(
        ErtCliError,
        match=(
            "Operation halted: ES-MDA requires weights to proceed. "
            "Please provide appropriate weights and try again."
        ),
    ):
        run_cli(parsed)


def test_ert_config_parser_fails_gracefully_on_unreadable_config_file(
    copy_case, caplog
):
    copy_case("snake_oil_field")
    config_file_name = "snake_oil_surface.ert"
    os.chmod(config_file_name, 0x0)
    caplog.set_level(logging.WARNING)

    with pytest.raises(OSError, match="[Pp]ermission"):
        ErtConfig.from_file(config_file_name)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_field_init_file_not_readable(copy_case, monkeypatch):
    monkeypatch.setattr(
        ensemble_evaluator._wait_for_evaluator, "WAIT_FOR_EVALUATOR_TIMEOUT", 5
    )
    copy_case("snake_oil_field")
    config_file_name = "snake_oil_field.ert"
    field_file_rel_path = "fields/permx0.grdecl"
    os.chmod(field_file_rel_path, 0x0)

    try:
        run_ert_test_run(config_file_name)
    except ErtCliError as err:
        assert "Permission denied:" in str(err)


def test_surface_init_fails_during_forward_model_callback(copy_case):
    copy_case("snake_oil_field")

    rng = np.random.default_rng()

    Path("./surface").mkdir()
    nx = 5
    ny = 10
    surf = xtgeo.RegularSurface(
        ncol=nx, nrow=ny, xinc=1.0, yinc=1.0, values=rng.standard_normal(size=(nx, ny))
    )
    surf.to_file("surface/surf_init_0.irap", fformat="irap_ascii")

    config_file_name = "snake_oil_surface.ert"
    parameter_name = "TOP"
    with open(config_file_name, mode="r+", encoding="utf-8") as config_file_handler:
        content_lines = config_file_handler.read().splitlines()
        index_line_with_surface_top = [
            index
            for index, line in enumerate(content_lines)
            if line.startswith(f"SURFACE {parameter_name}")
        ][0]
        line_with_surface_top = content_lines[index_line_with_surface_top]
        breaking_line_with_surface_top = line_with_surface_top
        content_lines[index_line_with_surface_top] = breaking_line_with_surface_top
        config_file_handler.seek(0)
        config_file_handler.write("\n".join(content_lines))

    try:
        run_ert_test_run(config_file_name)
    except ErtCliError as err:
        assert f"Failed to initialize parameter {parameter_name!r}" in str(err)


def test_unopenable_observation_config_fails_gracefully(copy_case):
    copy_case("snake_oil_field")
    config_file_name = "snake_oil_field.ert"
    with open(config_file_name, mode="r", encoding="utf-8") as config_file_handler:
        content_lines = config_file_handler.read().splitlines()
    index_line_with_observation_config = [
        index
        for index, line in enumerate(content_lines)
        if line.startswith("OBS_CONFIG")
    ][0]
    line_with_observation_config = content_lines[index_line_with_observation_config]
    observation_config_rel_path = line_with_observation_config.split(" ")[1]
    observation_config_abs_path = os.path.join(os.getcwd(), observation_config_rel_path)
    os.chmod(observation_config_abs_path, 0x0)

    with pytest.raises(
        ValueError,
        match="Do not have permission to open observation config file "
        f"{observation_config_abs_path!r}",
    ):
        run_ert_test_run(config_file_name)


def run_ert_test_run(config_file: str) -> None:
    parser = ArgumentParser(prog="test_run")
    parsed = ert_parser(
        parser,
        [
            TEST_RUN_MODE,
            config_file,
        ],
    )
    run_cli(parsed)


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(ENSEMBLE_SMOOTHER_MODE),
        pytest.param(ITERATIVE_ENSEMBLE_SMOOTHER_MODE),
        pytest.param(ES_MDA_MODE),
    ],
)
@pytest.mark.usefixtures("copy_poly_case")
def test_that_the_model_raises_exception_if_active_less_than_minimum_realizations(mode):
    """
    Verify that the run model checks that active realizations 20 is less than 100
    Omit testing of SingleTestRun because that executes with 1 active realization
    regardless of configuration.
    """
    with open("poly.ert", "r", encoding="utf-8") as fin, open(
        "poly_high_min_reals.ert", "w", encoding="utf-8"
    ) as fout:
        for line in fin:
            if "MIN_REALIZATIONS" in line:
                fout.write("MIN_REALIZATIONS 100")
            else:
                fout.write(line)

    args = Mock()
    args.config = "poly_high_min_reals.ert"
    parser = ArgumentParser(prog="test_main")

    ert_args = [
        mode,
        "poly_high_min_reals.ert",
        "--port-range",
        "1024-65535",
        "--realizations",
        "0-19",
        "--target-case",
    ]
    ert_args.append("testcase" if mode is ENSEMBLE_SMOOTHER_MODE else "testcase-%d")

    parsed = ert_parser(
        parser,
        ert_args,
    )

    with pytest.raises(
        ErtCliError,
        match="Number of active realizations",
    ):
        run_cli(parsed)


@pytest.mark.usefixtures("copy_poly_case")
def test_that_the_model_warns_when_active_realizations_less_min_realizations():
    """
    Verify that the run model checks that active realizations is equal or higher than
    NUM_REALIZATIONS when running ensemble_experiment.
    A warning is issued when NUM_REALIZATIONS is higher than active_realizations.
    """
    with open("poly.ert", "r", encoding="utf-8") as fin, open(
        "poly_lower_active_reals.ert", "w", encoding="utf-8"
    ) as fout:
        for line in fin:
            if "MIN_REALIZATIONS" in line:
                fout.write("MIN_REALIZATIONS 100")
            else:
                fout.write(line)

    args = Mock()
    args.config = "poly_lower_active_reals.ert"
    parser = ArgumentParser(prog="test_main")

    ert_args = [
        "ensemble_experiment",
        "poly_lower_active_reals.ert",
        "--port-range",
        "1024-65535",
        "--realizations",
        "0-4",
    ]

    parsed = ert_parser(
        parser,
        ert_args,
    )

    with pytest.warns(
        ConfigWarning,
        match="Due to active_realizations 5 is lower than MIN_REALIZATIONS",
    ):
        run_cli(parsed)


@pytest.mark.integration_test
@pytest.mark.usefixtures("copy_poly_case")
def test_failing_job_cli_error_message():
    # modify poly_eval.py
    with open("poly_eval.py", mode="a", encoding="utf-8") as poly_script:
        poly_script.writelines(["    raise RuntimeError('Argh')"])

    args = Mock()
    args.config = "poly_high_min_reals.ert"
    parser = ArgumentParser(prog="test_main")

    parser = ArgumentParser(prog="test_main")
    parsed = ert_parser(
        parser,
        [
            TEST_RUN_MODE,
            "poly.ert",
            "--port-range",
            "1024-65535",
        ],
    )
    expected_substrings = [
        "Realization: 0 failed after reaching max submit (2)",
        "job poly_eval failed",
        "Process exited with status code 1",
        "Traceback",
        "raise RuntimeError('Argh')",
        "RuntimeError: Argh",
    ]
    try:
        run_cli(parsed)
    except ErtCliError as error:
        for substring in expected_substrings:
            assert substring in f"{error}"
    else:
        pytest.fail(msg="Expected run cli to raise ErtCliError!")


@pytest.fixture
def setenv_config(tmp_path):
    config = tmp_path / "test.ert"

    # Given that environment variables are set in the config
    config.write_text(
        """
        NUM_REALIZATIONS 1
        SETENV FIRST first:$PATH
        SETENV SECOND $MYVAR
        SETENV MYVAR foo
        SETENV THIRD  TheThirdValue
        SETENV FOURTH fourth:$MYVAR
        INSTALL_JOB ECHO ECHO.txt
        FORWARD_MODEL ECHO
        """,
        encoding="utf-8",
    )
    run_script = tmp_path / "run.py"
    run_script.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        'print(os.environ["FIRST"])\n'
        'print(os.environ["SECOND"])\n'
        'print(os.environ["THIRD"])\n'
        'print(os.environ["FOURTH"])\n',
        encoding="utf-8",
    )
    os.chmod(run_script, 0o755)

    (tmp_path / "ECHO.txt").write_text(
        dedent(
            """
        EXECUTABLE run.py
        """
        )
    )
    return config


expected_vars = {
    "FIRST": "first:$PATH",
    "SECOND": "$MYVAR",
    "MYVAR": "foo",
    "THIRD": "TheThirdValue",
    "FOURTH": "fourth:$MYVAR",
}


def test_that_setenv_config_is_parsed_correctly(setenv_config):
    config = ErtConfig.from_file(str(setenv_config))
    # then res config should read the SETENV as is
    assert config.env_vars == expected_vars


def test_that_setenv_sets_environment_variables_in_jobs(setenv_config):
    # When running the jobs
    parser = ArgumentParser(prog="test_main")
    parsed = ert_parser(
        parser,
        [
            TEST_RUN_MODE,
            str(setenv_config),
            "--port-range",
            "1024-65535",
        ],
    )

    run_cli(parsed)

    # Then the environment variables are put into jobs.json
    with open("simulations/realization-0/iter-0/jobs.json", encoding="utf-8") as f:
        data = json.load(f)
        global_env = data.get("global_environment")
        assert global_env == expected_vars

    path = os.environ["PATH"]

    # and then job_dispatch should expand the variables on the compute side
    with open("simulations/realization-0/iter-0/ECHO.stdout.0", encoding="utf-8") as f:
        lines = f.readlines()
        assert len(lines) == 4
        # the compute-nodes path is the same since it's running locally,
        # so we can test that we can prepend to it
        assert lines[0].strip() == f"first:{path}"
        # MYVAR is not set in the compyte node yet, so it should not be expanded
        assert lines[1].strip() == "$MYVAR"
        # THIRD is just a simple value
        assert lines[2].strip() == "TheThirdValue"
        # now MYVAR now set, so should be expanded inside the value of FOURTH
        assert lines[3].strip() == "fourth:foo"
