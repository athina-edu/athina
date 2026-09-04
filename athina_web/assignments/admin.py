from django.contrib import admin

from .models import Assignment, Course, Student

admin.site.register(Assignment)
admin.site.register(Course)
admin.site.register(Student)
