import contextlib
import itertools
import os
import shutil
import zipfile
from glob import glob
from logging import getLogger
from requests.adapters import HTTPAdapter
from requests.exceptions import HTTPError
from requests.packages.urllib3.util.retry import Retry
from github3 import GitHub
from github3.session import GitHubSession
from cumulusci.utils import temporary_dir
from cumulusci.utils.git import parse_repo_url

ZIP_FILE_NAME = "archive.zip"

logger = getLogger(__name__)


class GitHubRetry(Retry):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def increment(self, *args, **kwargs):
        # Check for connnection and fail on SSLerror
        # SSLCertVerificationError
        if "error" in kwargs:
            error = kwargs["error"]
            error_str = "CERTIFICATE_VERIFY_FAILED"
            if error_str in str(error):
                raise error
        # finally call increment
        return super().increment(*args, **kwargs)


# Prepare request retry policy to be attached to github sessions.
# 401 is a weird status code to retry, but sometimes it happens spuriously
# and https://github.community/t5/GitHub-API-Development-and/Random-401-errors-after-using-freshly-generated-installation/m-p/22905 suggests retrying
retries = GitHubRetry(status_forcelist=(401, 502, 503, 504), backoff_factor=0.3)
adapter = HTTPAdapter(max_retries=retries)


def get_github_api_for_repo(repo_url, token, session=None) -> GitHub:
    if token is None:
        raise ValueError("No GitHub token provided")
    owner, repo_name, host = parse_repo_url(repo_url)
    gh = GitHub(
        session=session
        or GitHubSession(default_read_timeout=30, default_connect_timeout=30)
    )
    # Apply retry policy
    gh.session.mount("http://", adapter)
    gh.session.mount("https://", adapter)
    gh.login(token=token)
    gh = gh.repository(owner, repo_name)
    return gh


class UnsafeZipfileError(Exception):
    pass


def is_safe_path(path):
    return not os.path.isabs(path) and ".." not in path.split(os.path.sep)


def zip_file_is_safe(zip_file):
    return all(is_safe_path(info.filename) for info in zip_file.infolist())


def log_unsafe_zipfile_error(repo_url, commit_ish):
    """
    It is very unlikely that we will get an unsafe zipfile, as we get it
    from GitHub, but must be considered.
    """
    url = f"{repo_url}#{commit_ish}"
    logger.error(f"Malformed or malicious zip file from {url}.")


def extract_zip_file(zip_file, owner, repo_name):
    zip_file.extractall()
    # We know that the zipball contains a root directory named something
    # like this by GitHub's convention. If that ever breaks, this will
    # break:
    zipball_root = glob(f"{owner}-{repo_name}-*")[0]
    # It's not unlikely that the zipball root contains a directory with
    # the same name, so we pre-emptively rename it to probably avoid
    # collisions:
    shutil.move(zipball_root, "zipball_root")
    for path in itertools.chain(glob("zipball_root/*"), glob("zipball_root/.*")):
        shutil.move(path, ".")
    shutil.rmtree("zipball_root")
    os.remove(ZIP_FILE_NAME)


def get_zip_file(repo, commit_ish):
    success = repo.archive("zipball", path=ZIP_FILE_NAME, ref=commit_ish)
    if not success:  # pragma: no cover
        message = (
            "Cannot download zipfile. "
            "This may be caused by networking issues on the Metecho Server. "
            f"Please report this to the Metecho Admins. ({repo} : {commit_ish})"
        )
        raise HTTPError(message)
    return zipfile.ZipFile(ZIP_FILE_NAME)


@contextlib.contextmanager
def local_github_checkout(
    repo=None,  # github3.py repo object authenticated with token
    commit_ish=None,
):
    with temporary_dir() as repo_root:
        # pretend it's a git clone to satisfy cci
        os.mkdir(".git")

        if commit_ish == "#DEFAULT":
            commit_ish = repo.default_branch
        assert commit_ish, "Default branch should be supplied"

        zip_file = get_zip_file(repo, commit_ish)

        if not zip_file_is_safe(zip_file):
            log_unsafe_zipfile_error(repo.html_url, commit_ish)
            raise UnsafeZipfileError
        else:
            # Because subsequent operations require certain things to be
            # present in the filesystem at cwd, things that are in the
            # repo (we hope):
            extract_zip_file(zip_file, repo.owner.login, repo.name)
            yield repo_root
