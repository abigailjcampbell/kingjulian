from core.crud import crud_urls

from . import views as v

urlpatterns = crud_urls("", "inventoryitem", v.InventoryList, v.InventoryCreate,
                        v.InventoryUpdate, v.InventoryArchive)
