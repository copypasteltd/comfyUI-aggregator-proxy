-- Forward-only expansion. Existing image assets retain their original checks.
ALTER TABLE render_control_asset_uploads
    DROP CONSTRAINT render_control_asset_uploads_file_bytes_check,
    DROP CONSTRAINT render_control_asset_uploads_media_type_check;
ALTER TABLE render_control_asset_bindings
    DROP CONSTRAINT render_control_asset_bindings_file_bytes_check,
    DROP CONSTRAINT render_control_asset_bindings_media_type_check,
    DROP CONSTRAINT render_control_asset_bindings_width_check,
    DROP CONSTRAINT render_control_asset_bindings_height_check;

ALTER TABLE render_control_asset_uploads ADD CONSTRAINT render_control_upload_media_budget CHECK (
    file_bytes >= 1 AND (
        (media_type IN ('image/png','image/jpeg','image/webp') AND file_bytes <= 20971520) OR
        (media_type IN ('audio/wav','audio/flac','audio/mpeg','audio/ogg') AND file_bytes <= 268435456) OR
        (media_type IN ('video/mp4','video/webm','model/gltf-binary') AND file_bytes <= 536870912)
    )
);
ALTER TABLE render_control_asset_bindings ADD CONSTRAINT render_control_binding_media_budget CHECK (
    file_bytes >= 1 AND (
        (media_type IN ('image/png','image/jpeg','image/webp') AND file_bytes <= 20971520) OR
        (media_type IN ('audio/wav','audio/flac','audio/mpeg','audio/ogg') AND file_bytes <= 268435456) OR
        (media_type IN ('video/mp4','video/webm','model/gltf-binary') AND file_bytes <= 536870912)
    )
);
ALTER TABLE render_control_asset_bindings ADD CONSTRAINT render_control_binding_dimensions CHECK (
    (media_type IN ('image/png','image/jpeg','image/webp','video/mp4','video/webm')
        AND width BETWEEN 1 AND 25000000 AND height BETWEEN 1 AND 25000000)
    OR (media_type IN ('audio/wav','audio/flac','audio/mpeg','audio/ogg','model/gltf-binary') AND width=0 AND height=0)
);
