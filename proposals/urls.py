from django.urls import path

from proposals import views

urlpatterns = [
    path("", views.index, name="index"),
    path("p/", views.proposal_list, name="proposal_list"),
    path("p/<uuid:proposal_id>", views.proposal_detail, name="proposal_detail"),
    path("p/<uuid:proposal_id>/status.json", views.proposal_status, name="proposal_status"),
    path("p/<uuid:proposal_id>/docx", views.proposal_docx, name="proposal_docx"),
    path("access", views.access, name="access"),
]
