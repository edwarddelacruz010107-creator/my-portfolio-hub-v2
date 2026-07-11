"""Static regression coverage for the production SEO upload interface."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "app/templates/admin/seo.html"
ROUTE = ROOT / "app/admin/routes/profile_appearance.py"


def test_seo_drop_zone_attaches_file_and_validates_upload():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "Drop your photo here" in source
    assert "new DataTransfer()" in source
    assert "imageInput.files=transfer.files" in source
    assert "file.size>10*1024*1024" in source
    assert "handleImageFile(e.dataTransfer.files&&e.dataTransfer.files[0],true)" in source
    assert 'id="seoUploadStatus"' in source


def test_seo_previews_hide_missing_images_and_use_responsive_grid():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert ".seo-shell [hidden]{display:none!important}" in source
    assert ".seo-layout{display:grid;grid-template-columns:" in source
    assert "@media(max-width:1100px){.seo-layout{grid-template-columns:1fr}" in source
    assert "@media(max-width:760px)" in source
    assert "naturalWidth===0" in source


def test_replacement_is_saved_before_old_share_image_is_deleted():
    source = ROUTE.read_text(encoding="utf-8")
    save_position = source.index("new_image, upload_error = save_image", source.index("def seo_settings"))
    delete_position = source.index("delete_image(previous_image, 'profiles')", save_position)

    assert save_position < delete_position
    assert "and not replacement_uploaded and not upload_warning" in source
