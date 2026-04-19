import pytest

from erieiron_common.git_utils import GitWrapper


@pytest.mark.parametrize(
    "remote_url, expected",
    [
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("git@github.com:owner/repo.git", ("owner", "repo")),
        ("ssh://git@github.com/owner/repo.git", ("owner", "repo")),
        ("", None),
        ("https://example.com/not-github/repo.git", None),
    ],
)
def test_extract_github_repo_slug(remote_url, expected):
    assert GitWrapper._extract_github_repo_slug(remote_url) == expected

def test_get_latest_commit_uses_github_api(monkeypatch):
    def fake_fetch(owner, repo, repo_ref=None):
        assert (owner, repo, repo_ref) == ("example", "repo", None)
        return "abc123", "commit message"

    monkeypatch.setattr(
        "erieiron_common.git_utils.GitWrapper._fetch_commit_via_api",
        fake_fetch,
    )

    assert GitWrapper.get_latest_commit("https://github.com/example/repo.git") == (
        "abc123",
        "commit message",
    )


def test_get_commit_for_ref_uses_github_api(monkeypatch):
    def fake_fetch(owner, repo, repo_ref=None):
        assert (owner, repo, repo_ref) == ("example", "repo", "feature/ref")
        return "def456", "feature commit"

    monkeypatch.setattr(
        "erieiron_common.git_utils.GitWrapper._fetch_commit_via_api",
        fake_fetch,
    )

    assert GitWrapper.get_commit_for_ref(
        "https://github.com/example/repo.git",
        "feature/ref",
    ) == ("def456", "feature commit")


def test_fetch_commit_via_api_success(monkeypatch, tmp_path):
    git = GitWrapper(source_root=tmp_path)

    monkeypatch.setattr("erieiron_common.git_utils.get_github_token", lambda: "token")

    class DummyResponse:
        status_code = 200

        def json(self):
            return [
                {
                    "sha": "abc123",
                    "commit": {"message": "commit message"},
                }
            ]

    def fake_get(url, headers, params, timeout):
        assert url == "https://api.github.com/repos/example/repo/commits"
        assert headers["Authorization"] == "token token"
        assert params == {"per_page": 1}
        assert timeout == 10
        return DummyResponse()

    monkeypatch.setattr("erieiron_common.git_utils.requests.get", fake_get)

    assert git._fetch_commit_via_api("example", "repo") == ("abc123", "commit message")


def test_fetch_commit_via_api_non_200(monkeypatch, tmp_path):
    git = GitWrapper(source_root=tmp_path)

    monkeypatch.setattr("erieiron_common.git_utils.get_github_token", lambda: "token")

    class DummyResponse:
        status_code = 404
        text = "not found"

        def json(self):
            return []

    monkeypatch.setattr(
        "erieiron_common.git_utils.requests.get", lambda *args, **kwargs: DummyResponse()
    )

    with pytest.raises(Exception) as exc:
        git._fetch_commit_via_api("example", "repo")

    assert "GitHub API returned 404" in str(exc.value)
