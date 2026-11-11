import unittest

import renovate_workflow as w

TEMPLATE = (
    "jobs:\n"
    "  call-workflow:  \n"
    "    uses: rancher/renovate-config/.github/workflows/renovate-vault.yml@release"
    " # zizmor: ignore[unpinned-uses] Deployment pins it.\n"
    "    with: {}\n"
)


class RenderTest(unittest.TestCase):
    def test_pins_release_and_strips_template_comments(self):
        out = w.render_renovate_workflow(TEMPLATE, "a" * 40, "v1.2.3")
        self.assertEqual(
            out,
            "jobs:\n"
            "  call-workflow:\n"
            f"    uses: rancher/renovate-config/.github/workflows/renovate-vault.yml@{'a' * 40} # v1.2.3\n"
            "    with: {}\n",
        )

    def test_repins_existing_sha(self):
        old = w.render_renovate_workflow(TEMPLATE, "a" * 40, "v1.2.3")
        self.assertIn(f"@{'b' * 40} # v1.3.0\n", w.render_renovate_workflow(old, "b" * 40, "v1.3.0"))

    def test_auto_approve_repository(self):
        out = w.render_auto_approve("github/token/OWNER--REPOSITORY--auto-approve token\n", "rancher/fleet")
        self.assertEqual(out, "github/token/rancher--fleet--auto-approve token\n")


class ReleaseTest(unittest.TestCase):
    def test_highest_version_tag(self):
        tags = [
            {"name": "v1.0.9", "commit": {"sha": "old"}},
            {"name": "v1.0.13", "commit": {"sha": "new"}},
            {"name": "v2.0.0-rc1", "commit": {"sha": "rc"}},
        ]
        self.assertEqual(w.latest_release(lambda _: tags), ("new", "v1.0.13"))

    def test_fallback(self):
        self.assertEqual(w.latest_release(lambda _: None), w.FALLBACK_RELEASE)


if __name__ == "__main__":
    unittest.main()
