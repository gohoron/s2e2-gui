from __future__ import unicode_literals

from django.db import models

from django.db import models

class Document(models.Model):
    docfile = models.FileField(updload_to='documents')
