#include <csignal>
#include <cstdlib>
#include <mutex>
#include <optional>
#include <sys/wait.h>
#include <thread>

#include <ert/job_queue/local_driver.hpp>
#include <ert/job_queue/queue_driver.hpp>
#include <ert/job_queue/spawn.hpp>
#include <ert/util/util.hpp>

typedef struct local_job_struct local_job_type;

struct local_job_struct {
    bool active = false;
    job_status_type status = JOB_QUEUE_WAITING;
    std::optional<std::thread> run_thread = std::nullopt;
    pid_t child_process = 0;
};

struct local_driver_struct {
    std::mutex submit_lock;
};

static local_job_type *local_job_alloc() { return new local_job_type; }

job_status_type local_driver_get_job_status(void * /**__driver*/, void *__job) {
    if (__job != nullptr) {
        local_job_type *job = reinterpret_cast<local_job_type *>(__job);
        return job->status;
    }

    return JOB_QUEUE_NOT_ACTIVE; // The job has not been registered at all
}

void local_driver_free_job(void *__job) {
    local_job_type *job = reinterpret_cast<local_job_type *>(__job);
    if (!job->active)
        free(job);
}

void local_driver_kill_job(void * /**__driver*/, void *__job) {
    local_job_type *job = reinterpret_cast<local_job_type *>(__job);
    if (job->child_process > 0)
        kill(job->child_process, SIGTERM);
}

/**
  This function needs to dereference the job pointer after the waitpid() call is
  complete, it is therefore essential that no other threads have called free(job)
  while the external process is running.
*/
void submit_job_thread(const char *executable, int argc, char **argv,
                       local_job_type *job) {
    int wait_status;
    job->child_process =
        spawn(executable, argc, (const char **)argv, nullptr, nullptr);
    util_free_stringlist(argv, argc);
    waitpid(job->child_process, &wait_status, 0);

    job->active = false;
    job->status = JOB_QUEUE_EXIT;
    if (WIFEXITED(wait_status) != 0 && (WEXITSTATUS(wait_status) == 0))
        job->status = JOB_QUEUE_DONE;
}

void *local_driver_submit_job(void *__driver, const char *submit_cmd,
                              int /** num_cpu */, const char * /**run_path*/,
                              const char * /**job_name*/, int argc,
                              const char **argv) {
    local_driver_type *driver = reinterpret_cast<local_driver_type *>(__driver);
    local_job_type *job = local_job_alloc();
    auto argv_copy = util_alloc_stringlist_copy(argv, argc);

    std::lock_guard guard{driver->submit_lock};
    job->active = true;
    job->status = JOB_QUEUE_RUNNING;

    job->run_thread = std::thread{
        [=] { submit_job_thread(submit_cmd, argc, argv_copy, job); }};
    job->run_thread->detach();

    return job;
}

void local_driver_free(local_driver_type *driver) { delete driver; }

void local_driver_free__(void *__driver) {
    local_driver_type *driver = reinterpret_cast<local_driver_type *>(__driver);
    local_driver_free(driver);
}

void *local_driver_alloc() { return new local_driver_type; }

void local_driver_init_option_list(stringlist_type * /**option_list*/) {}

bool local_driver_set_option(void * /**__driver*/, const char * /**option_key*/,
                             const void * /**value_*/) {
    util_abort(
        "%s: Local driver does not support run time setting of options\n",
        __func__);
    return false;
}

const void *local_driver_get_option(const void * /**__driver*/,
                                    const char * /**option_key*/) {
    util_abort(
        "%s: Local driver does not support run time reading of options\n",
        __func__);
    return nullptr;
}
