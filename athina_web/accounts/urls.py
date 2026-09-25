# accounts/urls.py
from django.urls import path
from django.contrib.auth import logout
from django.shortcuts import redirect
from . import views


def logout_view(request):
    """Accept both GET and POST for logout (Django 5.x LogoutView is POST-only)."""
    logout(request)
    return redirect('/')


urlpatterns = [
    path('logout/', logout_view, name='logout'),
    path('profile/', views.profile, name='profile'),
    path('profile/git-repos/', views.gitlab_repos, name='gitlab_repos'),
    path('profile/llm-models/', views.llm_models, name='llm_models'),
    path('profile/test-resend/', views.test_resend, name='test_resend'),
    path('users/', views.user_list, name='user_list'),
    path('users/create/', views.create_user, name='create_user'),
    path('users/assign-tas/', views.assign_tas, name='assign_tas'),
    path('users/<int:user_id>/edit/', views.edit_user, name='edit_user'),
    path('users/<int:user_id>/delete/', views.delete_user, name='delete_user'),
    path('users/<int:user_id>/reset-password/', views.reset_password, name='reset_password'),
]