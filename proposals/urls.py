from django.http import HttpResponse
from django.urls import path


def index(request):
    return HttpResponse("Proposal Agent")


urlpatterns = [path("", index, name="index")]
