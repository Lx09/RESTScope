"""SQLAlchemy mappings owned by the persistence adapter."""

from .generator_config_orm import (
    GeneratorCatalogStateORM,
    GeneratorConfigRevisionORM,
    InputGeneratorConfigORM,
    OperationGeneratorConfigORM,
)
from .resource_catalog_orm import (
    OperationResourceRuleORM,
    ResourceAliasORM,
    ResourceIdentifierORM,
    ResourceMonitorErrorORM,
    ResourceOperationUsageORM,
    ResourceORM,
)
from .schema_orm import SchemaORM

__all__ = [
    "GeneratorCatalogStateORM",
    "GeneratorConfigRevisionORM",
    "InputGeneratorConfigORM",
    "OperationGeneratorConfigORM",
    "OperationResourceRuleORM",
    "ResourceAliasORM",
    "ResourceIdentifierORM",
    "ResourceMonitorErrorORM",
    "ResourceOperationUsageORM",
    "ResourceORM",
    "SchemaORM",
]
