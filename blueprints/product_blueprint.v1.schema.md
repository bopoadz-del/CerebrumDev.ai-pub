# product_blueprint.v1

Fail-closed blueprint schema consumed by `app.factory.blueprint.ProductBlueprint`.

Required fields: `schema_version` (=`product_blueprint.v1`), `product_id`, `product_name`,
`vertical`, `summary`, `capabilities` (non-empty).

Capability `block_ids` must be dual-registered in Cerebrum-Blocks `block_registry/` and
`backend/app/factory/shelves/factory_blocks.json` or planning fails with UNSUPPORTED.
