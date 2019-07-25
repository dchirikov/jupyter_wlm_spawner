import argparse
import sys
import json
import subprocess as sp
from threading import Timer
import atexit
from jupyter_client.kernelspec import  KernelSpecManager


DEFAULT_CMD_TIMEOUT = 30  # sec


def run_cmd(cmd, timeout=DEFAULT_CMD_TIMEOUT):
    """
    Returns 'rc', 'stdout', 'stderr', 'exception'
    Where 'exception' is a content of Python exception if any
    """
    rc = 255
    stdout, stderr, exception = "", "", ""
    try:
        proc = sp.Popen(
            cmd, shell=True,
            stdout=sp.PIPE, stderr=sp.PIPE
        )
        timer = Timer(
            timeout, lambda p: p.kill(), [proc]
        )
        try:
            timer.start()
            stdout, stderr = proc.communicate()
        except:
            exception = f"Timeout executing '{cmd}'"
        finally:
            timer.cancel()

        proc.wait()
        rc = proc.returncode
    except Exception as e:
        exception = e
    return rc, stdout, stderr, exception


class WLMSpawnerError(RuntimeError):
    pass


def get_scontrol_job_field(jobnum, what):
    res = None
    what += "="
    l = len(what)
    rc, stdout, stderr, exception = run_cmd(f"scontrol show job {jobnum}")
    if rc:
        raise WLMSpawnerError(f"Unable to get job status. Jobid: {jobnum}; stderr: {stderr}")
    show_job = str(stdout).split()
    for rec in show_job:
        if rec[:l] == what:
            res = rec[l:]
    if res is None:
        raise WLMSpawnerError(f"Unable to get job field {what} for job {jobnum}")
    return res


class WLMSpawner():

    def __init__(self):
        self.args = self.parse_arguments()
        self.connection = self.parse_connection_file(self.args.connection_file)
        self.spawn = eval(f"self._spawn_{self.args.scheduler}")
        self.kernel_spec = KernelSpecManager().get_kernel_spec(self.args.kernel)

    def parse_arguments(self):
        parser = argparse.ArgumentParser(description="WLM spawner")
        parser.add_argument(
                "-f", "--connection-file",
                help="Connection file",
                required=True
        )
        parser.add_argument(
                "-s", "--scheduler",
                choices=["slurm", "sge"],
                default="slurm",
                help="Scheduler type"
        )
        parser.add_argument(
                "-o", "--wlm-options",
                default="",
                help="Additional options for workload manager"
        )
        parser.add_argument(
                "-k", "--kernel",
                default="python3",
                help="Kernel to spawn"
        )
        parser.add_argument(
                "-e", "--env-commands",
                default="",
                help="Kernel to spawn"
        )

        return parser.parse_args()

    def parse_connection_file(self, connection_file):
        with open(connection_file) as f:
            connection_sett = json.load(f)
        return connection_sett

    def _spawn_sge(self):
        # TODO
        raise NotImplementedError

    def _spawn_slurm(self):
        # With -I don't wait inifinite time for allocation
        salloc_cmd = f"salloc -I{DEFAULT_CMD_TIMEOUT} "

        # cut spaces and quotes
        salloc_cmd += self.args.wlm_options.strip()[1:-1]

        salloc_cmd = salloc_cmd.split()
        salloc_proc = sp.Popen(
            salloc_cmd, shell=False,
            stdout=sp.PIPE, stderr=sp.PIPE, stdin=sp.PIPE,
            universal_newlines=True
        )

        # read first line
        salloc_line = str(salloc_proc.stderr.readline())

        # last element should be job number:
        try:
            jobnum = int(salloc_line.split()[-1])
        except ValueError:
            # didn't retur job number. Error.
            raise WLMSpawnerError(f"Unable to get jobid. Error on allocation: {salloc_line}")

        # delete job on exit
        atexit.register(sp.Popen, f"scancel {jobnum}".split(), shell=False)

        # check if job in RUNNING state
        job_state = "UNKNOWN"
        while job_state != "RUNNING":
            job_state = get_scontrol_job_field(jobnum, "JobState")
            if salloc_proc.poll():
                # -I in salloc did it's job
                raise WLMSpawnerError(f"Unable to get allocation.")

        # job in RUNNING state
        # get batch node
        batch_host = get_scontrol_job_field(jobnum, "BatchHost")[:-2]

        # Forward ports
        ssh_forwarding = f"ssh "
        ssh_forwarding += f"-L {self.connection['shell_port']}:localhost:{self.connection['shell_port']} "
        ssh_forwarding += f"-L {self.connection['iopub_port']}:localhost:{self.connection['iopub_port']} "
        ssh_forwarding += f"-L {self.connection['stdin_port']}:localhost:{self.connection['stdin_port']} "
        ssh_forwarding += f"-L {self.connection['control_port']}:localhost:{self.connection['control_port']} "
        ssh_forwarding += f"-L {self.connection['hb_port']}:localhost:{self.connection['hb_port']} "
        ssh_forwarding += f" -N {batch_host}"

        ssh_proc = sp.Popen(
                ssh_forwarding.split(),
                stdout=sp.PIPE, stderr=sp.PIPE,
                shell=False
        )
        # drop ssh tunnels on exit
        atexit.register(ssh_proc.terminate)

        # Copy kernel file to host
        dest_dir = "/".join(self.args.connection_file.split("/")[:-1])

        # create dir to store connection file
        ssh_mkdir = f"ssh {batch_host} mkdir -p {dest_dir}"
        rc, stdout, stderr, exception = run_cmd(ssh_mkdir)
        if rc:
            raise WLMSpawnerError(f"Unable to create dir to store connection_file: stderr: {stderr}")

        # chmod
        ssh_chmod = f"ssh {batch_host} chmod 1700 {dest_dir}"
        rc, stdout, stderr, exception = run_cmd(ssh_chmod)
        if rc:
            raise WLMSpawnerError(f"Unable to chmod on dir to store connection_file: stderr: {stderr}")

        # copy connection file
        scp_cmd = f"scp -pr {self.args.connection_file} {batch_host}:{self.args.connection_file}"
        rc, stdout, stderr, exception = run_cmd(scp_cmd)
        if rc:
            raise WLMSpawnerError(f"Unable to scp connection_file: stderr: {stderr}")

        # run real kernel
        kernel_cmd = self.get_real_kernel_cmd()
        env_commands = self.args.env_commands.strip()[1:-1]
        kernel_script = "set -e"
        kernel_script = "set -x"
        kernel_script += f"{env_commands}\n"
        kernel_script += f"srun -N 1 -E -w {batch_host} {kernel_cmd}\n"
        _ = salloc_proc.communicate(kernel_script)
        salloc_proc.wait()

        # Cleanup. Delete unnneded connection file
        ssh_delete_conn_file = f"ssh {batch_host} rm -f {self.args.connection_file}"
        rc, stdout, stderr, exception = run_cmd(ssh_delete_conn_file)
        if rc:
            raise WLMSpawnerError(f"Unable to delete connection file: stderr: {stderr}")

    def get_real_kernel_cmd(self):
        connection_file = self.args.connection_file
        ret = " ".join(self.kernel_spec.argv)
        return eval(f'f"""{ret}"""')

def main():
    wlm_spawner = WLMSpawner()
    wlm_spawner.spawn()
