from django.urls import path

from . import views

app_name = "chores"

urlpatterns = [
    path("chores/", views.chore_list, name="chore_list"),
    path("chores/add/", views.chore_add, name="chore_add"),
    path("chores/<int:pk>/complete/", views.chore_complete, name="chore_complete"),
    path("people/", views.person_list, name="person_list"),
    path("people/add/", views.person_add, name="person_add"),
    path("people/<int:pk>/remove/", views.person_remove_confirm, name="person_remove_confirm"),
    path("people/<int:pk>/delete/", views.person_remove, name="person_remove"),
    path("assign/", views.assign, name="assign"),
    path("status/", views.status, name="status"),
]
