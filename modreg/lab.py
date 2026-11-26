"""What every AAS of the lab starts from, where aas-model's own defaults say "Not specified"."""
from __future__ import annotations

from aas_model.constants import BASE_URL
from aas_model.resource_template.modules import LabNameplate
from aas_pydantic.submodel_templates.nameplate import (
    CountryOfOrigin, ManufacturerName, ManufacturerProductDesignation, OrderCodeOfManufacturer, URIOfTheProduct,
    YearOfConstruction,
)


def nameplate() -> LabNameplate:
    """The nameplate of the lab, which a resource or a product then states its own values in: the lab's
    own class, the one a module's type starts from, so a profile can state the difference to it."""
    return LabNameplate(
        id_short="Nameplate",
        URIOfTheProduct=URIOfTheProduct(value=f"{BASE_URL}/product/1"),
        ManufacturerName=ManufacturerName(value={"en": "AAU Smart Production Lab"}),
        ManufacturerProductDesignation=ManufacturerProductDesignation(value={"en": "Pharmaceutical Processing Resource"}),
        OrderCodeOfManufacturer=OrderCodeOfManufacturer(value=""),
        YearOfConstruction=YearOfConstruction(value="2026"),
        CountryOfOrigin=CountryOfOrigin(value="DK"),
    )
