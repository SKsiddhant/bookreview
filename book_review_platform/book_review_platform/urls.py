from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from accounts.views import GenreViewSet


def redirect_to_react(request):
    return redirect(settings.FRONTEND_URL)


router = DefaultRouter()
router.register(r'genres', GenreViewSet, basename='genre')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('ratings/', include('ratings.urls')),
    path('', redirect_to_react),
    path('reviews/', include('reviews.urls')),
]

urlpatterns += router.urls

# Serve uploaded media in development only (static() returns [] when DEBUG is off)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
