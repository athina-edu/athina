from django.urls import path
from . import views
from rest_framework.urlpatterns import format_suffix_patterns

urlpatterns = [
    path('', views.assignments, name='assignments'),
    # Course management
    path('courses/', views.course_list, name='course_list'),
    path('courses/new/', views.course_create, name='course_create'),
    path('courses/<int:course_id>/', views.course_detail, name='course_detail'),
    path('courses/<int:course_id>/edit/', views.course_create, name='course_edit'),
    path('courses/<int:course_id>/delete/', views.course_delete, name='course_delete'),
    # Student management within a course
    path('courses/<int:course_id>/students/', views.student_list, name='student_list'),
    path('courses/<int:course_id>/students/add/', views.student_add, name='student_add'),
    path('courses/<int:course_id>/students/import/', views.student_bulk_import, name='student_bulk_import'),
    path('courses/<int:course_id>/students/provision/', views.provision_students, name='provision_students'),
    path('courses/<int:course_id>/students/import/progress/', views.import_progress, name='import_progress'),
    path('courses/<int:course_id>/students/import/progress/api/', views.import_progress_api, name='import_progress_api'),
    path('courses/<int:course_id>/students/<int:student_id>/edit/', views.student_edit, name='student_edit'),
    path('courses/<int:course_id>/students/<int:student_id>/delete/', views.student_delete, name='student_delete'),
    # Assignment management
    path('new/', views.assignment_create, name='create_assignment'),
    path('<int:assignment_id>/', views.assignment_view, name='assignment_view'),
    path('<int:assignment_id>/log', views.assignment_log, name='assignment_log'),
    path('<int:assignment_id>/edit', views.assignment_create, name='assignment_edit'),
    path('<int:assignment_id>/delete', views.assignment_delete, name='assignment_delete'),
    path('<int:assignment_id>/force/<int:user_id>', views.assignment_force, name='assignment_force'),
    path('<int:assignment_id>/report/<int:user_id>/<str:report_type>', views.assignment_report, name='assignment_report'),
    path('<int:assignment_id>/guidance/<int:user_id>', views.assignment_guidance, name='assignment_guidance'),
    path('api/', views.APIView.as_view(), name="api"),
    path('webhook/', views.push_event, name="webhook"),
]

urlpatterns = format_suffix_patterns(urlpatterns)
