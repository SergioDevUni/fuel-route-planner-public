from django.urls import path

from app.api.views import live, ready, trip_map, trip_result, trips

urlpatterns = [
    path("health/live", live),
    path("health/ready", ready),
    path("api/v1/trips", trips),
    path("api/v1/trips/<str:trip_id>", trip_result),
    path("maps/<str:trip_id>", trip_map),
]
