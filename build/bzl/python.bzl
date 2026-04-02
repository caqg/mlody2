load("@aspect_rules_py//py:defs.bzl", "py_binary", "py_library", "py_pex_binary", "py_test")

def o_py_test(name, deps = [], imports = None, **kwargs):
    extra_deps = []
    extra_imports = imports if imports != None else ["."]

    if "@pip//pytest" not in deps:
        extra_deps.append("@pip//pytest")

    if "@pip//debugpy" not in deps:
        extra_deps.append("@pip//debugpy")

    py_test(
        name = name,
        pytest_main = True,
        imports = extra_imports,
        deps = deps + extra_deps,
        **kwargs
    )

def o_py_library(name, imports = None, **kwargs):
    extra_imports = imports if imports != None else ["."]
    py_library(
        name = name,
        imports = extra_imports,
        **kwargs
    )

def o_py_binary(name, imports = None, **kwargs):
    extra_imports = imports if imports != None else ["."]
    py_binary(
        name = name,
        imports = extra_imports,
        **kwargs
    )

def o_py_pex_binary(name, **kwargs):
    py_pex_binary(
        name = name,
        **kwargs
    )
