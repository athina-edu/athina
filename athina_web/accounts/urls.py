# accounts/urls.py
from django.urls import path

from . import views


urlpatterns = [
    path('profile/', views.profile, name='profile'),
    path('profile/git-repos/', views.gitlab_repos, name='gitlab_repos'),
    path('profile/llm-models/', views.llm_models, name='llm_models'),
    path('users/', views.user_list, name='user_list'),
    path('users/create/', views.create_user, name='create_user'),
    path('users/assign-tas/', views.assign_tas, name='assign_tas'),
    path('users/<int:user_id>/delete/', views.delete_user, name='delete_user'),
]