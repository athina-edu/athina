import random
import re
from urllib.parse import quote_plus as urlquote
from urllib.parse import urlparse

from athina.url import *

__all__ = ('gitlab_set_webhook', 'gitlab_check_if_repo_private',)


def gitlab_return_encoded_url(repository_url):
    return urlquote(re.sub('\.git$', '', urlparse(repository_url).path[1:]))


def gitlab_set_webhook(configuration, logger, user_values):
    if configuration.athina_web_url is not None:
        logger.logger.info("Attempting to set webhook for user %s" % user_values.user_id)
        encoded_url = gitlab_return_encoded_url(user_values.repository_url)
        webhook_url = "%s/assignments/webhook/" % configuration.athina_web_url
        webhook_token = random.getrandbits(128)
        # Check if hook exists
        data = request_url("https://%s/api/v4/projects/%s/hooks" % (configuration.git_url, encoded_url),
                           headers={"Authorization": "Bearer %s" % configuration.git_password},
                           method="get", return_type="json")
        try:
            hook_id = [w.get('id') for w in data if w.get('url', '') == webhook_url]
        except AttributeError:
            return None
        if len(hook_id) == 0:
            # Add new webhook
            data = request_url("https://%s/api/v4/projects/%s/hooks" % (configuration.git_url, encoded_url),
                               headers={"Authorization": "Bearer %s" % configuration.git_password},
                               payload={"id": encoded_url,
                                        "url": webhook_url,
                                        "push_events": "yes",
                                        "enable_ssl_verification": "no",
                                        "token": webhook_token
                                        }, method="post", return_type="json")
        else:
            # Edit existing webhook
            data = request_url("https://%s/api/v4/projects/%s/hooks/%s" %
                               (configuration.git_url, encoded_url, hook_id[0]),
                               headers={"Authorization": "Bearer %s" % configuration.git_password},
                               payload={"id": encoded_url,
                                        "url": webhook_url,
                                        "push_events": "yes",
                                        "enable_ssl_verification": "no",
                                        "token": webhook_token
                                        }, method="put", return_type="json")
        if data.get("created_at", None) is not None:
            user_values.use_webhook = True
            user_values.webhook_token = webhook_token
            user_values.save()
            logger.logger.info("Webhook was set successfully.")
        else:
            logger.logger.warning("Attempt to send webhook failed!")
            logger.logger.debug(data)


def gitlab_check_if_repo_private(configuration, logger, repository_url):
    if configuration.gitlab_check_repo_is_private:
        logger.logger.info("Checking if repository is private. "
                           "This can be disable in configuration (gitlab_check_repo_is_private)")
        encoded_url = gitlab_return_encoded_url(repository_url)
        data = request_url("https://%s/api/v4/projects/%s" % (configuration.git_url, encoded_url),
                           headers={"Authorization": "Bearer %s" % configuration.git_password},
                           method="get", return_type="json")
        visibility = data.get("visibility", None)
        if visibility == "private" or visibility is None:  # it is either private or we cannot check if it is
            return True
        else:
            return False
    else:
        return True
