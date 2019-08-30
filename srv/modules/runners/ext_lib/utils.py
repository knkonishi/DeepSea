from salt.client import LocalClient, salt
from salt.runner import RunnerClient
from salt.config import client_config
import logging

log = logging.getLogger(__name__)


def runner(opts):
    """ TODO: docstring """
    log.debug("Initializing runner")
    runner = RunnerClient(opts)
    __master_opts__ = client_config("/etc/salt/master")
    __master_opts__['quiet'] = False
    qrunner = RunnerClient(__master_opts__)
    return qrunner


def cluster_minions():
    """ Technically this should come from select.py

    TODO:
    Move select.py in this realm (/ext_lib) and make it python-import consumable
    """
    log.debug("Searching for cluster_minions")
    potentials = LocalClient().cmd(
        __utils__['deepsea_minions.show'](),
        'test.ping', tgt_type='compound')
    minions = list()
    for k, v in potentials.items():
        if v:
            minions.append(k)
    return minions


def prompt(message,
           options='(y/n)',
           non_interactive=False,
           default_answer=False):
    """ TODO: docstring """
    if non_interactive:
        log.debug(
            f"running in non-interactive mode. default answer is {default_answer}"
        )
        return default_answer
    answer = input(f"{message} - {options}")
    if answer.lower() == 'y' or answer.lower() == 'Y':
        return True
    elif answer.lower() == 'n' or answer.lower() == 'N':
        return False
    else:
        answer = input(f"You typed {answer}. We accept {options}")
        prompt(message, options=options)


def evaluate_module_return(job_data):
    failed = False
    for minion_id, result in job_data.items():
        log.debug(f"results for job on minion: {minion_id} is: {result}")
        if not result:
            print(f"Module call failed on {minion_id}")
            failed = True

    if failed:
        return False
    return True


def evaluate_state_return(job_data):
    """ TODO """
    # does log.x actually log in the salt log? I don't think so..
    failed = False
    for minion_id, job_data in job_data.items():
        log.debug(f"{job_data} ran on {minion_id}")
        for jid, metadata in job_data.items():
            log.debug(f"Job {jid} run under: {metadata.get('name', 'n/a')}")
            log.debug(
                f"Job {jid } was successful: {metadata.get('result', False)}")
            if not metadata.get('result', False):
                log.debug(
                    f"Job {metadata.get('name', 'n/a')} failed on minion: {minion_id}"
                )
                print(
                    f"Job {metadata.get('name', 'n/a')} failed on minion: {minion_id}"
                )
                failed = True
    if failed:
        return False
    return True


def log_n_print(message):
    """ TODO: docstring """
    # TODO: I assume I have to pass a context logger to this function when invoked from a salt_module
    # this lib is not executed with the salt context, hence no logging will end up in the salt-master logs
    log.debug(message)
    print(message)


def _get_candidates(role=None):
    """ TODO: docstring """
    # Is this the right appracoh or should cephprocesses be used again?
    assert role
    all_minions = LocalClient().cmd(
        f"roles:{role}", f'{role}.already_running', tgt_type='pillar')

    candidates = list()

    for k, v in all_minions.items():
        if not v:
            candidates.append(k)
    return candidates


def _is_running(role_name=None, minion=None, func='wait_role_up'):
    """ TODO: docstring """
    assert role_name
    search = f"I@role:{role_name}"
    running = True
    if minion:
        search = minion
    log_n_print("Checking if processes are running. This may take a while..")
    minions_return = LocalClient().cmd(
        search,
        f'cephprocesses.{func}', [f"role={role_name}"],
        tgt_type='compound')
    for minion, status in minions_return.items():
        # TODO: Refactor the 'wait_role_down' function. This is horrible
        if status:
            log_n_print(f"role-{role_name} is running on {minion}")
            log_n_print(f"This is showing the wrong status for role deletion currently")
        if not status:
            log_n_print(f"This is showing the wrong status for role deletion currently")
            log_n_print(f"role-{role_name} is *NOT* running on {minion}")
            running = False
    return running


def _remove_role(role=None, non_interactive=False):
    # TODO: already_running vs is_running
    # find process id vs. systemd
    ##
    ## There is mon ok-to-rm, ok-to-stop, ok-to-add-offline
    ##
    """ TODO: docstring """
    assert role
    already_running = LocalClient().cmd(
        f"not I@roles:{role}",
        f'{role}.already_running',
        tgt_type='compound')
    to_remove = [k for (k, v) in already_running.items() if v]
    if not to_remove:
        print("Nothing to remove. Exiting..")
        return True
    if prompt(
            f"""Removing role: {role} on minion {', '.join(to_remove)}
Continue?""",
            non_interactive=non_interactive,
            default_answer=True):
        print(f"Removing {role} on {' '.join(to_remove)}")
        ret: str = LocalClient().cmd(
            to_remove,
            f'podman.remove_{role}',
            ['registry.suse.de/devel/storage/6.0/images/ses/6/ceph/ceph'],
            tgt_type='list')
        if not evaluate_module_return(ret):
            return False

        ret = [is_running(minion, role_name=role, func='wait_role_down') for minion in to_remove]
        # TODO: do proper checks here:
        if all(ret):
            print(f"{role} deletion was successful.")
            return True
        return False


    else:
        return 'aborted'


def _deploy_role(role=None, non_interactive=False):
    assert role
    candidates = _get_candidates(role=role)
    if candidates:
        if prompt(
                f"""These minions will be {role}: {', '.join(candidates)}
Continue?""",
                non_interactive=non_interactive,
                default_answer=True):
            print("Deploying..")

            if role == 'mgr':
                for candidate in candidates:
                    # create and register keyring
                    ret: str = LocalClient().cmd(
                        "roles:master",
                        f'podman.create_mgr_keyring',
                        ['registry.suse.de/devel/storage/6.0/images/ses/6/ceph/ceph', candidate],
                        tgt_type='pillar')


                if not evaluate_module_return(ret):
                    return False

                # distrubute keyring
                ret: str = LocalClient().cmd(
                    "roles:mgr",
                    'state.apply',
                    ['ceph.mgr.keyring'],
                    tgt_type='pillar')

                if not evaluate_state_return(ret):
                    return False


            ret: str = LocalClient().cmd(
                candidates,
                f'podman.create_{role}',
                ['registry.suse.de/devel/storage/6.0/images/ses/6/ceph/ceph'],
                tgt_type='list')

            if not evaluate_module_return(ret):
                return False

            # TODO: query in a loop with a timeout
            # TODO: Isn't that what we have in cephproceses.wait?
            # TODO: Check that.
            ret = [is_running(minion, role_name=role) for minion in candidates]
            if not all(ret):
                print(f"{role} deployment was not successful.")
                return False
            return True

        return False
    else:
        print(f"No candidates for a {role} deployment found")
        return True


def is_running(minion, role_name=None, func='wait_role_up'):
    assert role_name
    if _is_running(role_name=role_name, minion=minion, func=func):
        return True
    return False


def master_minion():
    '''
    Load the master modules
    '''
    __master_opts__ = salt.config.client_config("/etc/salt/master")
    __master_utils__ = salt.loader.utils(__master_opts__)
    __salt_master__ = salt.loader.minion_mods(
        __master_opts__, utils=__master_utils__)

    return __salt_master__["master.minion"]()