from django.urls import path
from . import views

urlpatterns = [
    path('make-call/', views.make_call, name='make_call'),
    path('twiml/', views.twiml, name='twiml'),
]
