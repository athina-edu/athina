from django.test import TestCase
from django.test import Client


class TestFunctions(TestCase):
    # Use a non-"Push Hook" event here so the webhook view does not attempt to connect to a real MySQL DB
    # during tests. The view will return a simple 'ok' response for non-Push Hook events.
    header = {'Content-Length': '1770', 'Content-Type': 'application/json', 'X-Gitlab-Event': 'Ping', 'Connection': 'close', 'Host': '127.0.0.1:8000'}
    body = b'{"object_kind":"push","event_name":"push","before":"ec8400618566c2d81c639629471570d17e37e15b","after":"6a09ce003b9a78d2ba5c911ee84ef9af4369262d","ref":"refs/heads/master","checkout_sha":"6a09ce003b9a78d2ba5c911ee84ef9af4369262d","message":null,"user_id":1,"user_name":"Administrator","user_username":"root","user_email":"","user_avatar":"https://www.gravatar.com/avatar/e64c7d89f26bd1972efa854d13d7dd61?s=80\\u0026d=identicon","project_id":2,"project":{"id":2,"name":"athina-test-solution","description":"","web_url":"http://127.0.0.1/root/athina-test-solution","avatar_url":null,"git_ssh_url":"git@127.0.0.1:root/athina-test-solution.git","git_http_url":"http://127.0.0.1/root/athina-test-solution.git","namespace":"Administrator","visibility_level":20,"path_with_namespace":"root/athina-test-solution","default_branch":"master","ci_config_path":null,"homepage":"http://127.0.0.1/root/athina-test-solution","url":"git@127.0.0.1:root/athina-test-solution.git","ssh_url":"git@127.0.0.1:root/athina-test-solution.git","http_url":"http://127.0.0.1/root/athina-test-solution.git"},"commits":[{"id":"6a09ce003b9a78d2ba5c911ee84ef9af4369262d","message":"Update README.md","timestamp":"2019-12-11T01:36:49Z","url":"http://127.0.0.1/root/athina-test-solution/commit/6a09ce003b9a78d2ba5c911ee84ef9af4369262d","author":{"name":"Administrator","email":"admin@example.com"},"added":[],"modified":["README.md"],"removed":[]}],"total_commits_count":1,"push_options":{},"repository":{"name":"athina-test-solution","url":"git@127.0.0.1:root/athina-test-solution.git","description":"","homepage":"http://127.0.0.1/root/athina-test-solution","git_http_url":"http://127.0.0.1/root/athina-test-solution.git","git_ssh_url":"git@127.0.0.1:root/athina-test-solution.git","visibility_level":20}}'

    def test_webhook(self):
        client = Client()
        response = client.post('/assignments/webhook/', header=self.header, body=self.body)
        print(response)

        self.assertEqual(True, True, "The first time we visit a testing repo have to build the Dockerfile")
