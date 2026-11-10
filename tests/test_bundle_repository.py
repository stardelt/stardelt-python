"""``bundle._repository``: the web address a deploy records for its repository, never a credential."""

import subprocess

import pytest

from stardelt import bundle


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.example:team/shop.git", "https://github.example/team/shop"),
        ("ssh://git@git.example:2222/team/shop.git", "https://git.example/team/shop"),
        ("https://ada:s3cret@git.example/group/sub/shop.git", "https://git.example/group/sub/shop"),
        ("https://git.example/team/shop/", "https://git.example/team/shop"),
        ("file:///srv/shop", None),
    ],
)
def test_the_web_address_of_origin_without_user_token_port_or_git(tmp_path, url, expected):
    git(tmp_path, "init")
    git(tmp_path, "remote", "add", "origin", url)
    assert bundle._repository(tmp_path) == expected


def test_a_checkout_without_a_remote_has_no_repository(tmp_path):
    git(tmp_path, "init")
    assert bundle._repository(tmp_path) is None
