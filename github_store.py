"""
GitHub-backed storage for sources.py and archive files.
Reads GITHUB_TOKEN from Streamlit secrets (deployed) or env vars (local).
"""

import os


def _get_config():
    try:
        import streamlit as st
        token = st.secrets.get("GITHUB_TOKEN", "")
        repo_name = st.secrets.get("GITHUB_REPO", "evontay/evon-newsletter")
        branch = st.secrets.get("GITHUB_BRANCH", "master")
        if token:
            return token, repo_name, branch
    except Exception:
        pass
    return (
        os.getenv("GITHUB_TOKEN", ""),
        os.getenv("GITHUB_REPO", "evontay/evon-newsletter"),
        os.getenv("GITHUB_BRANCH", "master"),
    )


def get_repo():
    from github import Github
    token, repo_name, branch = _get_config()
    if not token:
        raise ValueError("GITHUB_TOKEN is not set. Add it to Streamlit secrets or your .env file.")
    return Github(token).get_repo(repo_name), branch


def write_file(path, content, commit_message):
    """Create or update a file in the GitHub repo."""
    from github import GithubException
    repo, branch = get_repo()
    try:
        existing = repo.get_contents(path, ref=branch)
        repo.update_file(path, commit_message, content, existing.sha, branch=branch)
    except GithubException:
        repo.create_file(path, commit_message, content, branch=branch)


def list_directory(path):
    """Return a list of (filename, decoded_content) tuples for text files in a repo directory."""
    from github import GithubException
    repo, branch = get_repo()
    try:
        contents = repo.get_contents(path, ref=branch)
        if not isinstance(contents, list):
            contents = [contents]
        return [
            (f.name, f.decoded_content.decode("utf-8"))
            for f in contents
            if not f.name.startswith(".")
        ]
    except GithubException:
        return []


def list_filenames(path):
    """Return just the filenames in a repo directory — fast, no content fetch."""
    from github import GithubException
    repo, branch = get_repo()
    try:
        contents = repo.get_contents(path, ref=branch)
        if not isinstance(contents, list):
            contents = [contents]
        return [f.name for f in contents if not f.name.startswith(".")]
    except GithubException:
        return []


def read_file_bytes(path):
    """Read a binary file (e.g. MP3) from the repo and return raw bytes."""
    from github import GithubException
    repo, branch = get_repo()
    try:
        f = repo.get_contents(path, ref=branch)
        return f.decoded_content
    except GithubException:
        return None
