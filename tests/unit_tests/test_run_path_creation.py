import os
from datetime import datetime
from pathlib import Path
from textwrap import dedent

import pytest

from ert.config import ErtConfig
from ert.enkf_main import EnKFMain
from ert.run_context import RunContext
from ert.runpaths import Runpaths
from ert.storage import StorageAccessor


@pytest.mark.usefixtures("use_tmpdir")
def test_that_run_template_replace_symlink_does_not_write_to_source(prior_ensemble):
    """This test is meant to test that we can have a symlinked file in the
    run path before we do replacement on a target file with the same name,
    the described behavior is:
    >     If the target_file already exists as a symbolic link, the
    >     symbolic link will be removed prior to creating the instance,
    >     ensuring that a remote file is not updated.
    it also has the side effect of testing that we are able to create the
    run path although the expected folders are already present
    """
    config_text = dedent(
        """
        NUM_REALIZATIONS 1
        JOBNAME my_case%d
        RUN_TEMPLATE template.tmpl result.txt
        """
    )
    Path("template.tmpl").write_text("I want to replace: <IENS>", encoding="utf-8")
    Path("config.ert").write_text(config_text, encoding="utf-8")
    ert_config = ErtConfig.from_file("config.ert")
    ert = EnKFMain(ert_config)
    run_context = ert.ensemble_context(prior_ensemble, [True], iteration=0)
    run_path = Path(run_context[0].runpath)
    os.makedirs(run_path)
    # Write a file that will be symlinked into the run run path with the
    # same name as the target_file
    Path("start.txt").write_text(
        "I dont want to replace in this file", encoding="utf-8"
    )
    os.symlink("start.txt", run_path / "result.txt")
    ert.createRunPath(run_context)
    assert (run_path / "result.txt").read_text(
        encoding="utf-8"
    ) == "I want to replace: 0"
    # Check that the source of the symlinked file is not updated
    assert (
        Path("start.txt").read_text(encoding="utf-8")
        == "I dont want to replace in this file"
    )


@pytest.mark.usefixtures("use_tmpdir")
def test_run_template_replace_in_file_with_custom_define(prior_ensemble):
    """
    This test checks that we are able to magically replace custom magic
    strings using the DEFINE keyword
    """
    config_text = dedent(
        """
        NUM_REALIZATIONS 1
        JOBNAME my_case%d
        DEFINE <MY_VAR> my_custom_variable
        RUN_TEMPLATE template.tmpl result.txt
        """
    )
    Path("template.tmpl").write_text("I WANT TO REPLACE:<MY_VAR>", encoding="utf-8")
    Path("config.ert").write_text(config_text, encoding="utf-8")

    ert_config = ErtConfig.from_file("config.ert")
    ert = EnKFMain(ert_config)
    run_context = ert.ensemble_context(prior_ensemble, [True], iteration=0)
    ert.createRunPath(run_context)
    assert (
        Path(run_context[0].runpath) / "result.txt"
    ).read_text() == "I WANT TO REPLACE:my_custom_variable"


@pytest.mark.usefixtures("use_tmpdir")
@pytest.mark.parametrize(
    "key, expected",
    [
        ("<DATE>", datetime.date(datetime.today()).isoformat()),
        ("<NUM_CPU>", "1"),
        ("<CONFIG_FILE_BASE>", "config"),
        ("<CONFIG_FILE>", "config.ert"),
        ("<ERT-CASE>", "prior"),
        ("<ERTCASE>", "prior"),
        ("<ECL_BASE>", "my_case0"),
        ("<ECLBASE>", "my_case0"),
        ("<IENS>", "0"),
        ("<ITER>", "0"),
    ],
)
def test_run_template_replace_in_file(key, expected, prior_ensemble):
    config_text = dedent(
        """
        NUM_REALIZATIONS 1
        JOBNAME my_case%d
        RUN_TEMPLATE template.tmpl result.txt
        """
    )
    Path("template.tmpl").write_text(f"I WANT TO REPLACE:{key}", encoding="utf-8")
    Path("config.ert").write_text(config_text, encoding="utf-8")

    ert_config = ErtConfig.from_file("config.ert")
    ert = EnKFMain(ert_config)
    run_context = ert.ensemble_context(prior_ensemble, [True], iteration=0)
    ert.createRunPath(run_context)
    assert (Path(run_context[0].runpath) / "result.txt").read_text(
        encoding="utf-8"
    ) == f"I WANT TO REPLACE:{expected}"


@pytest.mark.usefixtures("use_tmpdir")
@pytest.mark.parametrize(
    "ecl_base, expected_file",
    (
        ("MY_ECL_BASE", "MY_ECL_BASE.DATA"),
        ("relative/path/MY_ECL_BASE", "relative/path/MY_ECL_BASE.DATA"),
        ("MY_ECL_BASE%d", "MY_ECL_BASE0.DATA"),
        ("MY_ECL_BASE<IENS>", "MY_ECL_BASE0.DATA"),
    ),
)
def test_run_template_replace_in_ecl(ecl_base, expected_file, prior_ensemble):
    config_text = dedent(
        f"""
        NUM_REALIZATIONS 1
        ECLBASE {ecl_base}
        RUN_TEMPLATE BASE_ECL_FILE.DATA <ECLBASE>.DATA
        """
    )
    Path("BASE_ECL_FILE.DATA").write_text(
        "I WANT TO REPLACE:<NUM_CPU>", encoding="utf-8"
    )
    Path("config.ert").write_text(config_text, encoding="utf-8")

    ert_config = ErtConfig.from_file("config.ert")
    ert = EnKFMain(ert_config)
    run_context = ert.ensemble_context(prior_ensemble, [True], iteration=0)
    ert.createRunPath(run_context)
    assert (
        Path(run_context[0].runpath) / expected_file
    ).read_text() == "I WANT TO REPLACE:1"


@pytest.mark.usefixtures("use_tmpdir")
@pytest.mark.parametrize(
    "key, expected",
    [
        ("<DATE>", datetime.date(datetime.today()).isoformat()),
        ("<NUM_CPU>", "1"),
        ("<CONFIG_FILE_BASE>", "config"),
        ("<CONFIG_FILE>", "config.ert"),
        ("<ERT-CASE>", "prior"),
        ("<ERTCASE>", "prior"),
        ("<ECL_BASE>", "ECL_CASE0"),
        ("<ECLBASE>", "ECL_CASE0"),
        ("<IENS>", "0"),
        ("<ITER>", "0"),
    ],
)
def test_run_template_replace_in_ecl_data_file(key, expected, prior_ensemble):
    """
    This test that we copy the DATA_FILE into the runpath,
    do substitutions and rename it from the DATA_FILE name
    to ECLBASE
    """
    config_text = dedent(
        """
        NUM_REALIZATIONS 1
        ECLBASE ECL_CASE<IENS>
        DATA_FILE MY_DATA_FILE.DATA
        """
    )
    Path("MY_DATA_FILE.DATA").write_text(f"I WANT TO REPLACE:{key}", encoding="utf-8")
    Path("config.ert").write_text(config_text, encoding="utf-8")

    ert_config = ErtConfig.from_file("config.ert")
    ert = EnKFMain(ert_config)
    run_context = ert.ensemble_context(prior_ensemble, [True], iteration=0)
    ert.createRunPath(run_context)
    assert (Path(run_context[0].runpath) / "ECL_CASE0.DATA").read_text(
        encoding="utf-8"
    ) == f"I WANT TO REPLACE:{expected}"


@pytest.mark.usefixtures("use_tmpdir")
def test_run_template_replace_in_file_name(prior_ensemble):
    """
    This test checks that we are able to magically replace custom magic
    strings using the DEFINE keyword
    """
    config_text = dedent(
        """
        NUM_REALIZATIONS 1
        JOBNAME my_case%d
        DEFINE <MY_FILE_NAME> result.txt
        RUN_TEMPLATE template.tmpl <MY_FILE_NAME>
        """
    )
    Path("template.tmpl").write_text(
        "Not important, name of the file is important", encoding="utf-8"
    )
    Path("config.ert").write_text(config_text, encoding="utf-8")

    ert_config = ErtConfig.from_file("config.ert")
    ert = EnKFMain(ert_config)
    run_context = ert.ensemble_context(prior_ensemble, [True], iteration=0)
    ert.createRunPath(run_context)
    assert (
        Path(run_context[0].runpath) / "result.txt"
    ).read_text() == "Not important, name of the file is important"


@pytest.mark.usefixtures("use_tmpdir")
def test_that_sampling_prior_makes_initialized_fs(prior_ensemble):
    """
    This checks that creating the run path initializes the selected case,
    for that parameters are needed, so add a simple GEN_KW.
    """
    config_text = dedent(
        """
        NUM_REALIZATIONS 1
        JOBNAME my_case%d
        GEN_KW KW_NAME template.txt kw.txt prior.txt
        """
    )
    Path("template.tmpl").write_text(
        "Not important, name of the file is important", encoding="utf-8"
    )
    Path("config.ert").write_text(config_text, encoding="utf-8")
    with open("template.txt", "w", encoding="utf-8") as fh:
        fh.writelines("MY_KEYWORD <MY_KEYWORD>")
    with open("prior.txt", "w", encoding="utf-8") as fh:
        fh.writelines("MY_KEYWORD NORMAL 0 1")
    ert_config = ErtConfig.from_file("config.ert")
    ert = EnKFMain(ert_config)
    run_context = ert.ensemble_context(prior_ensemble, [True], iteration=0)
    assert not prior_ensemble.is_initalized
    ert.sample_prior(run_context.sim_fs, run_context.active_realizations)
    assert prior_ensemble.is_initalized


@pytest.mark.parametrize(
    "eclipse_data, expected_cpus",
    [
        ("PARALLEL 4 /", 4),
        pytest.param(
            dedent(
                """
            SLAVES
            -- comment
            -- comment with slash / "
            'upper' 'base' '*' 'data_file' 4 /
            'lower' 'base' '*' 'data_file_lower' /
            /"""
            ),
            6,
            id=(
                "Entry number 5 on each lines says how many cpus each "
                "slave should run on, omitting it means 1 cpu. "
                "1 for master, 4 for slave 1 and 1 for slave 2 = 6"
            ),
        ),
    ],
)
@pytest.mark.usefixtures("use_tmpdir")
def test_that_data_file_sets_num_cpu(eclipse_data, expected_cpus):
    config_text = dedent(
        """
        NUM_REALIZATIONS 1
        DATA_FILE MY_DATA_FILE.DATA
        """
    )
    Path("MY_DATA_FILE.DATA").write_text(eclipse_data, encoding="utf-8")
    Path("config.ert").write_text(config_text, encoding="utf-8")

    ert_config = ErtConfig.from_file("config.ert")
    ert = EnKFMain(ert_config)
    assert int(ert.get_context()["<NUM_CPU>"]) == expected_cpus


@pytest.mark.usefixtures("use_tmpdir")
def test_that_runpath_substitution_remain_valid(prior_ensemble):
    """
    This checks that runpath substitution remain intact.
    """
    config_text = dedent(
        """
        NUM_REALIZATIONS 2
        JOBNAME my_case%d
        RUNPATH realization-%d/iter-%d
        FORWARD_MODEL COPY_DIRECTORY(<FROM>=<CONFIG_PATH>/, <TO>=<RUNPATH>/)
        """
    )
    Path("config.ert").write_text(config_text, encoding="utf-8")

    ert_config = ErtConfig.from_file("config.ert")
    ert = EnKFMain(ert_config)

    run_context = ert.ensemble_context(prior_ensemble, [True, True], iteration=0)
    ert.createRunPath(run_context)

    for i, realization in enumerate(run_context):
        assert str(Path().absolute()) + "/realization-" + str(i) + "/iter-0" in Path(
            realization.runpath + "/jobs.json"
        ).read_text(encoding="utf-8")


@pytest.mark.parametrize("itr", [0, 1, 2, 17])
def test_write_snakeoil_runpath_file(snake_oil_case, storage, itr):
    ert = snake_oil_case
    experiment_id = storage.create_experiment(
        parameters=ert.ensembleConfig().parameter_configuration
    )
    prior_ensemble = storage.create_ensemble(
        experiment_id, name="prior", ensemble_size=25
    )

    num_realizations = 25
    mask = [True] * num_realizations
    mask[13] = False
    runpath_fmt = (
        "simulations/<GEO_ID>/realization-<IENS>/iter-<ITER>/"
        "magic-real-<IENS>/magic-iter-<ITER>"
    )
    jobname_fmt = "SNAKE_OIL_%d"
    global_substitutions = ert.get_context()
    for i in range(num_realizations):
        global_substitutions[f"<GEO_ID_{i}_{itr}>"] = str(10 * i)

    run_context = RunContext(
        sim_fs=prior_ensemble,
        runpaths=Runpaths(
            jobname_fmt,
            runpath_fmt,
            "a_file_name",
            global_substitutions.substitute_real_iter,
        ),
        initial_mask=mask,
        iteration=itr,
    )

    ert.sample_prior(run_context.sim_fs, run_context.active_realizations)
    ert.createRunPath(run_context)

    for i, _ in enumerate(run_context):
        if not mask[i]:
            continue

        assert os.path.isdir(f"simulations/{10*i}")

    runpath_list_path = "a_file_name"
    assert os.path.isfile(runpath_list_path)

    exp_runpaths = [
        runpath_fmt.replace("<ITER>", str(itr))
        .replace("<IENS>", str(iens))
        .replace("<GEO_ID>", str(10 * iens))
        for iens, _ in enumerate(run_context)
        if mask[iens]
    ]
    exp_runpaths = list(map(os.path.realpath, exp_runpaths))

    with open(runpath_list_path, "r", encoding="utf-8") as f:
        dumped_runpaths = list(zip(*[line.split() for line in f.readlines()]))[1]

    assert list(exp_runpaths) == list(dumped_runpaths)


@pytest.mark.usefixtures("use_tmpdir")
def test_assert_export(prior_ensemble):
    # Write a minimal config file with env
    with open("config_file.ert", "w", encoding="utf-8") as fout:
        fout.write(
            dedent(
                """
        NUM_REALIZATIONS 1
        JOBNAME a_name_%d
        RUNPATH_FILE directory/test_runpath_list.txt
        """
            )
        )
    ert_config = ErtConfig.from_file("config_file.ert")
    ert = EnKFMain(ert_config)
    runpath_list_file = ert.runpath_list_filename
    assert not runpath_list_file.exists()

    run_context = ert.ensemble_context(
        prior_ensemble,
        [True] * ert.getEnsembleSize(),
        iteration=0,
    )
    ert.sample_prior(run_context.sim_fs, run_context.active_realizations)
    ert.createRunPath(run_context)

    assert runpath_list_file.exists()
    assert runpath_list_file.name == "test_runpath_list.txt"
    assert (
        runpath_list_file.read_text("utf-8")
        == f"000  {os.getcwd()}/simulations/realization-0/iter-0  a_name_0  000\n"
    )


def _create_runpath(enkf_main: EnKFMain, storage: StorageAccessor) -> RunContext:
    """
    Instantiate an ERT runpath. This will create the parameter coefficients.
    """
    run_context = enkf_main.ensemble_context(
        storage.create_ensemble(
            storage.create_experiment(),
            name="prior",
            ensemble_size=enkf_main.getEnsembleSize(),
        ),
        [True] * enkf_main.getEnsembleSize(),
        iteration=0,
    )
    enkf_main.createRunPath(run_context)
    return run_context


@pytest.mark.parametrize(
    "append,numcpu",
    [
        ("", 1),  # Default is 1
        ("NUM_CPU 2\n", 2),
        ("DATA_FILE DATA\n", 8),  # Data file dictates NUM_CPU with PARALLEL
        ("NUM_CPU 3\nDATA_FILE DATA\n", 3),  # Explicit NUM_CPU supersedes PARALLEL
    ],
)
def test_num_cpu_subst(monkeypatch, tmp_path, append, numcpu, storage):
    """
    Make sure that <NUM_CPU> is substituted to the correct values
    """
    monkeypatch.chdir(tmp_path)

    (tmp_path / "test.ert").write_text(
        "JOBNAME test_%d\n"
        "NUM_REALIZATIONS 1\n"
        "INSTALL_JOB dump DUMP\n"
        "FORWARD_MODEL dump\n" + append
    )
    (tmp_path / "DATA").write_text("PARALLEL 8 /")
    (tmp_path / "DUMP").write_text("EXECUTABLE echo\nARGLIST <NUM_CPU>\n")

    config = ErtConfig.from_file(str(tmp_path / "test.ert"))
    enkf_main = EnKFMain(config)
    _create_runpath(enkf_main, storage)

    with open("simulations/realization-0/iter-0/jobs.json", encoding="utf-8") as f:
        assert f'"argList": ["{numcpu}"]' in f.read()
