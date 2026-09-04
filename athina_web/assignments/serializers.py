from rest_framework import serializers
from athina_web.assignments.models import Assignment
from django.conf import settings
import git


class AssignmentListSerializer(serializers.ModelSerializer):
    """Serializer to map the Model instance into JSON format."""
    directory = serializers.SerializerMethodField()
    course_id = serializers.SerializerMethodField()
    assignment_id = serializers.SerializerMethodField()

    def get_directory(self, obj):
        # Always reset to remote — local changes are discarded in favor of git
        try:
            repo = git.Repo('%s/%s/' % (settings.BASE_DIR, obj.absolute_path))
            repo.remote().fetch()
            repo.git.reset("--hard", "origin/master")
        except (git.exc.InvalidGitRepositoryError, git.exc.GitCommandError, git.exc.NoSuchPathError):
            pass
        return '%s/%s/' % (settings.BASE_DIR, obj.absolute_path)

    def get_course_id(self, obj):
        """Return course_id from the FK relationship."""
        return obj.course_id if obj.course_id else obj.pk

    def get_assignment_id(self, obj):
        """Return assignment_id (the Django model PK)."""
        return obj.pk

    class Meta:
        """Meta class to map serializer's fields with the model fields."""
        model = Assignment
        fields = ('directory', 'simulate', 'course_id', 'assignment_id')
