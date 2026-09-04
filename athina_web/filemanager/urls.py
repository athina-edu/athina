from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('<str:inner_path>', views.index, name='index'),
    path('upload/', views.upload, name='upload'),
    path('<str:inner_path>/upload/', views.upload, name='upload'),
    path('<str:inner_path>/view/', views.view_file, name='view_file'),
    path('<str:inner_path>/view/<str:reverse>', views.view_file, name='view_file'),
]
