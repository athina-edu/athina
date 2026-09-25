import os
import re
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from athina_web.filemanager import views as filemanager_views

VIEWER_RE = re.compile(r'<pre id="fileViewer".*?>(.*?)</pre>', re.S)


def viewer_body(response):
    """The text rendered inside the viewer pane."""
    match = VIEWER_RE.search(response.content.decode())
    return match.group(1) if match else ''


class TailLinesTests(TestCase):
    """`_tail_lines` is the size guard that keeps auto-refresh cheap."""

    def test_returns_text_unchanged_when_under_limit(self):
        text = "one\ntwo\nthree"
        self.assertEqual(filemanager_views._tail_lines(text, 10), (text, 0))

    def test_returns_last_lines_and_dropped_count(self):
        text = "\n".join("line%d" % i for i in range(100))
        tail, dropped = filemanager_views._tail_lines(text, 10)
        self.assertEqual(dropped, 90)
        self.assertEqual(tail.splitlines(), ["line%d" % i for i in range(90, 100)])

    def test_limit_of_zero_disables_truncation(self):
        text = "\n".join("line%d" % i for i in range(100))
        self.assertEqual(filemanager_views._tail_lines(text, 0), (text, 0))


@override_settings(MEDIA_ROOT='viewer_media', SECURE_SSL_REDIRECT=False)
class LogViewerTests(TestCase):
    """The file viewer caps height, tails logs, and offers log-only controls."""

    def setUp(self):
        self.tmp_base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_base, True)
        self.override = override_settings(BASE_DIR=self.tmp_base)
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.user = User.objects.create_user(username='prof', password='x')
        self.client.force_login(self.user)

        # Files live under BASE_DIR/MEDIA_ROOT/<owner_id>/ (see filemanager.utils).
        self.owner_dir = os.path.join(self.tmp_base, 'viewer_media', str(self.user.id))
        self.log_dir = os.path.join(self.owner_dir, 'logs')
        os.makedirs(self.log_dir)

    def _write(self, name, contents):
        path = os.path.join(self.log_dir, name)
        with open(path, 'w') as fh:
            fh.write(contents)
        return 'logs|%s' % name

    def _get(self, inner_path, **kwargs):
        url = reverse('filemanager:view_file', kwargs=dict({'inner_path': inner_path}, **kwargs))
        return self.client.get(url)

    def test_requires_login(self):
        self.client.logout()
        response = self._get(self._write('athina.log', 'hello'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_log_file_uses_console_pane_and_controls(self):
        response = self._get(self._write('athina.log', 'first\nsecond\n'))
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('log-viewer', html)
        self.assertNotIn('file-viewer', html)
        self.assertIn('id="followToggle"', html)
        self.assertIn('id="refreshToggle"', html)

    def test_rotated_log_is_also_treated_as_a_log(self):
        response = self._get(self._write('athina.yaml.log.1', 'first\nsecond\n'))
        self.assertIn('log-viewer', response.content.decode())

    def test_non_log_file_has_no_log_controls(self):
        response = self._get(self._write('notes.txt', 'plain text'))
        html = response.content.decode()
        self.assertIn('file-viewer', html)
        self.assertNotIn('log-viewer', html)
        self.assertNotIn('followToggle', html)

    def test_long_log_is_tailed_to_the_most_recent_lines(self):
        total = filemanager_views.MAX_LOG_LINES + 250
        contents = "\n".join("line%d" % i for i in range(total))
        response = self._get(self._write('athina.log', contents))
        html = response.content.decode()
        body = viewer_body(response)

        self.assertIn('line%d' % (total - 1), body)            # newest kept
        self.assertNotIn('\nline0\n', body)                   # oldest dropped
        self.assertEqual(body.splitlines()[0], 'line%d' % (total - filemanager_views.MAX_LOG_LINES))
        self.assertIn('earlier', html)
        self.assertIn('hidden', html)

    def test_short_log_is_not_truncated(self):
        response = self._get(self._write('athina.log', 'alpha\nomega'))
        self.assertIn('alpha', viewer_body(response))
        self.assertNotIn('earlier', response.content.decode())

    def test_existing_entries_render_newest_last(self):
        """Auto-scroll relies on the newest entry sitting at the bottom."""
        response = self._get(self._write('athina.log', 'oldest\nnewest'))
        body = viewer_body(response)
        self.assertLess(body.index('oldest'), body.index('newest'))

    def test_reverse_route_reverses_the_lines(self):
        response = self._get(self._write('notes.txt', 'alpha\nbeta'), reverse='reverse')
        html = response.content.decode()
        self.assertIn('log-viewer', html)
        body = viewer_body(response)
        self.assertLess(body.index('beta'), body.index('alpha'))

    def test_reverse_route_tails_long_output(self):
        contents = "\n".join("line%d" % i for i in range(filemanager_views.MAX_LOG_LINES + 50))
        body = viewer_body(self._get(self._write('notes.txt', contents), reverse='reverse'))
        # Reversed + tailed: the first entry (oldest of the kept window) is last.
        self.assertTrue(body.startswith('line49'))
        self.assertNotIn('line0\n', body)

    def test_missing_file_returns_404(self):
        response = self._get('logs|does-not-exist.log')
        self.assertEqual(response.status_code, 404)

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            self._get('..|..|etc|passwd')
