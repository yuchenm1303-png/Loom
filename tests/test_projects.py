"""The project registry: identity, naming, and what removal does not do."""

from __future__ import annotations

import os

import pytest

from app.projects import (
    MAX_NAME_LENGTH,
    Project,
    ProjectStore,
    ProjectStoreError,
    clean_name,
    default_name,
    normalize_root,
)


@pytest.fixture()
def store(tmp_path):
    return ProjectStore(tmp_path / "home")


def _folder(tmp_path, name: str):
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_a_project_is_named_after_its_folder_by_default(store, tmp_path):
    project = store.create(_folder(tmp_path, "loom"))

    assert project.name == "loom"
    assert project.project_id.startswith("p")


def test_an_explicit_name_wins(store, tmp_path):
    project = store.create(_folder(tmp_path, "loom"), name="  Work  project ")

    assert project.name == "Work project"


def test_a_root_belongs_to_exactly_one_project(store, tmp_path):
    folder = _folder(tmp_path, "loom")

    first = store.create(folder)
    second = store.create(folder, name="Different name")

    # Adding a folder that is already a project is a request for it to be in
    # the list, and it already is.
    assert second.project_id == first.project_id
    assert len(store.list()) == 1


def test_the_same_folder_spelled_differently_is_the_same_project(store, tmp_path):
    folder = _folder(tmp_path, "Loom")
    store.create(folder)

    # Windows in particular reaches the same directory by several spellings.
    variant = str(folder).replace(os.sep, "/")
    assert store.for_workspace(variant) is not None
    if os.name == "nt":
        assert store.for_workspace(str(folder).upper()) is not None


def test_a_missing_folder_cannot_become_a_project(store, tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        store.create(tmp_path / "nope")


def test_a_file_cannot_become_a_project(store, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="not a directory"):
        store.create(target)


def test_renaming_keeps_the_identity_and_the_root(store, tmp_path):
    project = store.create(_folder(tmp_path, "loom"))

    renamed = store.rename(project.project_id, "Loom Desktop")

    assert renamed.project_id == project.project_id
    assert renamed.root == project.root
    assert store.get(project.project_id).name == "Loom Desktop"


def test_an_empty_rename_is_refused(store, tmp_path):
    project = store.create(_folder(tmp_path, "loom"))

    with pytest.raises(ValueError, match="must not be empty"):
        store.rename(project.project_id, "   ")


def test_a_long_name_is_bounded(store, tmp_path):
    project = store.create(_folder(tmp_path, "loom"), name="x" * 500)

    assert len(project.name) == MAX_NAME_LENGTH


def test_removing_a_project_leaves_the_folder_alone(store, tmp_path):
    folder = _folder(tmp_path, "loom")
    (folder / "keep.txt").write_text("still here", encoding="utf-8")
    project = store.create(folder)

    store.remove(project.project_id)

    assert store.list() == ()
    assert (folder / "keep.txt").read_text(encoding="utf-8") == "still here"


def test_removing_an_unknown_project_is_an_error(store):
    with pytest.raises(KeyError):
        store.remove("p000000000000")


def test_projects_are_listed_by_name(store, tmp_path):
    store.create(_folder(tmp_path, "zebra"))
    store.create(_folder(tmp_path, "alpha"))
    store.create(_folder(tmp_path, "Mango"))

    assert [project.name for project in store.list()] == ["alpha", "Mango", "zebra"]


def test_adopting_registers_only_unknown_roots(store, tmp_path):
    known = _folder(tmp_path, "known")
    fresh = _folder(tmp_path, "fresh")
    store.create(known)

    added = store.adopt([str(known), str(fresh), str(known)])

    assert [project.name for project in added] == ["fresh"]
    assert len(store.list()) == 2


def test_adopting_skips_a_folder_that_no_longer_exists(store, tmp_path):
    gone = tmp_path / "deleted-project"

    assert store.adopt([str(gone)]) == ()
    assert store.list() == ()


def test_the_registry_survives_a_reload(tmp_path):
    home = tmp_path / "home"
    ProjectStore(home).create(_folder(tmp_path, "loom"), name="Loom")

    assert [project.name for project in ProjectStore(home).list()] == ["Loom"]


def test_a_corrupt_registry_is_reported_rather_than_silently_reset(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "projects.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ProjectStoreError):
        ProjectStore(home).list()


def test_an_unknown_registry_version_is_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "projects.json").write_text('{"version": 99, "projects": []}', encoding="utf-8")

    with pytest.raises(ProjectStoreError):
        ProjectStore(home).list()


def test_a_project_id_must_be_well_formed():
    with pytest.raises(ValueError, match="invalid project id"):
        Project(project_id="nope", name="x", root=".")


def test_default_name_falls_back_for_a_drive_root():
    assert default_name(normalize_root(os.path.abspath(os.sep))).strip() != ""


def test_clean_name_collapses_whitespace():
    assert clean_name("  a   b \n c ") == "a b c"
