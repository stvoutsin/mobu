SELECT '{{ username }}', 'object-several', *
FROM dp01_dc2_catalogs.object
WHERE objectId IN ({{ objects }})
