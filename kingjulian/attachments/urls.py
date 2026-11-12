from django.urls import path

from . import views as v

urlpatterns = [
    path("", v.AttachmentList.as_view(), name="attachment_list"),
    path("upload/<str:kind>/<int:pk>/", v.AttachmentUpload.as_view(), name="attachment_upload"),
    path("<int:pk>/", v.AttachmentDetail.as_view(), name="attachment_edit"),
    path("<int:pk>/download/", v.AttachmentDownload.as_view(), name="attachment_download"),
    path("<int:pk>/view/", v.AttachmentInline.as_view(), name="attachment_view"),
    path("<int:pk>/delete/", v.AttachmentDelete.as_view(), name="attachment_delete"),
]
