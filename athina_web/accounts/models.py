from django.db import models
from django.contrib.auth.models import User


class UserProfile(models.Model):
    """Stores per-user Git hosting credentials and role information."""
    ROLE_ADMIN = 'admin'
    ROLE_FACULTY = 'faculty'
    ROLE_TA = 'ta'
    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Admin'),
        (ROLE_FACULTY, 'Faculty'),
        (ROLE_TA, 'Teaching Assistant'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_FACULTY)

    # TA → Faculty assignment: which faculty members does this TA work for?
    managed_by = models.ManyToManyField(User, blank=True, related_name='ta_members',
                                         help_text="Faculty members this TA assists (for TAs only).")

    # --- GitLab ---
    gitlab_enabled = models.BooleanField(default=False)
    gitlab_url = models.CharField(max_length=255, default="gitlab.com", blank=True)
    gitlab_username = models.CharField(max_length=255, default="", blank=True)
    gitlab_token = models.CharField(max_length=255, default="", blank=True)

    # --- GitHub ---
    github_enabled = models.BooleanField(default=False)
    github_username = models.CharField(max_length=255, default="", blank=True)
    github_token = models.CharField(max_length=255, default="", blank=True)

    # --- LLM (AI Feedback) ---
    llm_enabled = models.BooleanField(default=False)
    llm_endpoint_url = models.CharField(max_length=512, default="https://api.openai.com/v1", blank=True,
                                         help_text="OpenAI-compatible endpoint URL")
    llm_api_key = models.CharField(max_length=512, default="", blank=True)
    llm_model = models.CharField(max_length=128, default="gpt-4o-mini", blank=True,
                                  help_text="Model name, e.g. gpt-4o-mini, gpt-4o, claude-3-haiku")

    # Legacy fields
    git_username = models.CharField(max_length=255, default="", blank=True)
    git_password = models.CharField(max_length=255, default="", blank=True)

    def __str__(self):
        return "%s (%s)" % (self.user.username, self.get_role_display())

    def get_active_providers(self):
        providers = []
        if self.gitlab_enabled and self.gitlab_username and self.gitlab_token:
            providers.append({'name': 'gitlab', 'label': 'GitLab',
                              'url': self.gitlab_url, 'username': self.gitlab_username})
        if self.github_enabled and self.github_username and self.github_token:
            providers.append({'name': 'github', 'label': 'GitHub',
                              'url': 'github.com', 'username': self.github_username})
        return providers

    def can_manage_course(self, course):
        """Can this user manage (edit/view) a given course?"""
        if self.user.is_superuser or self.role == self.ROLE_ADMIN:
            return True
        if self.role == self.ROLE_FACULTY and course.owner == self.user.id:
            return True
        if self.role == self.ROLE_TA and course.owner_id in self.managed_by.values_list('id', flat=True):
            return True
        return False

    def can_create_users(self):
        return self.user.is_superuser or self.role in (self.ROLE_ADMIN, self.ROLE_FACULTY)

    def can_create_faculty(self):
        return self.user.is_superuser or self.role == self.ROLE_ADMIN

    def save(self, *args, **kwargs):
        if self.gitlab_enabled and self.gitlab_username:
            self.git_username = self.gitlab_username
            self.git_password = self.gitlab_token
        super().save(*args, **kwargs)


def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


# Auto-create profile when a User is created
from django.db.models.signals import post_save
post_save.connect(create_user_profile, sender=User)
