/*
 * DecisionsAI in-bundle interpreter.
 *
 * Homebrew/venv python re-execs Python.app, so TCC labels the process "Python".
 * This Mach-O stays inside decisions.app so System Settings shows DecisionsAI.
 */
#include <Python.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int path_is_dir(const char *path)
{
    struct stat st;
    return path && path[0] && stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

static int self_path(char *out, uint32_t cap)
{
    if (_NSGetExecutablePath(out, &cap) != 0) {
        return -1;
    }
    char real[PATH_MAX];
    if (realpath(out, real) != NULL) {
        strncpy(out, real, cap);
        out[cap - 1] = '\0';
    }
    return 0;
}

static void dirname_copy(const char *path, char *out, size_t n)
{
    char tmp[PATH_MAX];
    strncpy(tmp, path, sizeof tmp - 1);
    tmp[sizeof tmp - 1] = '\0';
    strncpy(out, dirname(tmp), n - 1);
    out[n - 1] = '\0';
}

static int venv_root(char *out, size_t n)
{
    const char *explicit = getenv("DECISIONS_PYTHON");
    if (explicit && explicit[0]) {
        char bin[PATH_MAX];
        dirname_copy(explicit, bin, sizeof bin);
        dirname_copy(bin, out, n);
        if (path_is_dir(out)) {
            return 0;
        }
    }
    const char *home = getenv("HOME");
    if (!home || !home[0]) {
        return -1;
    }
    snprintf(out, n, "%s/.virtualenvs/decisions", home);
    return path_is_dir(out) ? 0 : -1;
}

static int read_pyvenv(const char *venv, char *exec_line, size_t en, char *ver, size_t vn)
{
    char cfg[PATH_MAX];
    snprintf(cfg, sizeof cfg, "%s/pyvenv.cfg", venv);
    FILE *fp = fopen(cfg, "r");
    if (!fp) {
        return -1;
    }
    char line[PATH_MAX];
    exec_line[0] = '\0';
    ver[0] = '\0';
    while (fgets(line, sizeof line, fp)) {
        char *eq = strchr(line, '=');
        if (!eq) {
            continue;
        }
        *eq = '\0';
        char *key = line;
        char *val = eq + 1;
        while (*key == ' ' || *key == '\t') {
            key++;
        }
        char *key_end = key + strlen(key);
        while (key_end > key && (key_end[-1] == ' ' || key_end[-1] == '\t')) {
            *--key_end = '\0';
        }
        while (*val == ' ' || *val == '\t') {
            val++;
        }
        char *nl = strchr(val, '\n');
        if (nl) {
            *nl = '\0';
        }
        if (strcmp(key, "executable") == 0) {
            strncpy(exec_line, val, en - 1);
            exec_line[en - 1] = '\0';
        } else if (strcmp(key, "version") == 0 && !ver[0]) {
            strncpy(ver, val, vn - 1);
            ver[vn - 1] = '\0';
        }
    }
    fclose(fp);
    if (ver[0]) {
        char *dot = strchr(ver, '.');
        if (dot) {
            dot = strchr(dot + 1, '.');
            if (dot) {
                *dot = '\0';
            }
        }
    }
    return exec_line[0] ? 0 : -1;
}

static int python_home_from_venv(const char *venv, char *out, size_t n, char *ver, size_t vn)
{
    char executable[PATH_MAX];
    if (read_pyvenv(venv, executable, sizeof executable, ver, vn) == 0) {
        char bin[PATH_MAX], version_dir[PATH_MAX];
        dirname_copy(executable, bin, sizeof bin);
        dirname_copy(bin, version_dir, sizeof version_dir);
        if (path_is_dir(version_dir)) {
            strncpy(out, version_dir, n - 1);
            out[n - 1] = '\0';
            return 0;
        }
    }
    return -1;
}

static int add_site_packages(const char *venv, const char *ver)
{
    char sitepkg[PATH_MAX];
    snprintf(sitepkg, sizeof sitepkg, "%s/lib/python%s/site-packages", venv, ver[0] ? ver : "3.12");
    PyObject *site = PyImport_ImportModule("site");
    if (!site) {
        return -1;
    }
    PyObject *result = PyObject_CallMethod(site, "addsitedir", "s", sitepkg);
    Py_DECREF(site);
    if (!result) {
        return -1;
    }
    Py_DECREF(result);
    return 0;
}

int main(int argc, char **argv)
{
    char exe[PATH_MAX];
    if (self_path(exe, sizeof exe) != 0) {
        fprintf(stderr, "decisions-python: cannot resolve executable path\n");
        return 1;
    }

    char venv[PATH_MAX];
    if (venv_root(venv, sizeof venv) != 0) {
        fprintf(stderr, "decisions-python: venv not found (set DECISIONS_PYTHON)\n");
        return 1;
    }
    setenv("VIRTUAL_ENV", venv, 0);

    char home[PATH_MAX];
    char ver[16];
    if (python_home_from_venv(venv, home, sizeof home, ver, sizeof ver) != 0) {
        fprintf(stderr, "decisions-python: cannot read %s/pyvenv.cfg\n", venv);
        return 1;
    }

    PyStatus status;
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    status = PyConfig_SetBytesString(&config, &config.executable, exe);
    if (PyStatus_Exception(status)) {
        goto fail;
    }
    status = PyConfig_SetBytesString(&config, &config.home, home);
    if (PyStatus_Exception(status)) {
        goto fail;
    }
    status = PyConfig_SetBytesArgv(&config, argc, argv);
    if (PyStatus_Exception(status)) {
        goto fail;
    }

    status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        Py_ExitStatusException(status);
    }

    if (add_site_packages(venv, ver) != 0) {
        PyErr_Print();
        return 1;
    }
    return Py_RunMain();

fail:
    PyConfig_Clear(&config);
    Py_ExitStatusException(status);
    return 1;
}
