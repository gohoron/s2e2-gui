from views import upload_file
from django.conf.urls import url
from . import views

urlpatterns = [
    url(r'^$', upload_file),
]
