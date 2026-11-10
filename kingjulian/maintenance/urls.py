from django.urls import path

from core.crud import crud_urls

from . import views as v

urlpatterns = crud_urls("tasks", "task", v.TaskList, v.TaskCreate, v.TaskUpdate, v.TaskArchive) + [
    path("", v.OccurrenceList.as_view(), name="occurrence_list"),
    path("<int:pk>/", v.OccurrenceUpdate.as_view(), name="occurrence_edit"),
    path("<int:pk>/complete/", v.OccurrenceComplete.as_view(), name="occurrence_complete"),
    path("<int:pk>/skip/", v.OccurrenceSkip.as_view(), name="occurrence_skip"),
]
