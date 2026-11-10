from core.crud import crud_urls

from . import views as v

urlpatterns = crud_urls("", "contact", v.ContactList, v.ContactCreate, v.ContactUpdate, v.ContactArchive)
