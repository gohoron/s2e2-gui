# -*- coding: utf-8 -*-
# Generated by Django 1.11 on 2017-05-22 13:52
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Analysis',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('s2e_num', models.IntegerField()),
                ('binary_checksum', models.BigIntegerField()),
                ('binary_name', models.CharField(max_length=256)),
            ],
        ),
    ]
