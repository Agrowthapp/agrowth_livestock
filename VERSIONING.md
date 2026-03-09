# Guía de Versionado - AgrowthApp

Esta guía detalla el proceso de versionado para los módulos de la organización AgrowthApp, siguiendo el estándar de **Semantic Versioning (SemVer)**.

## ¿Qué es SemVer?

El versionado semántico utiliza un formato `MAJOR.MINOR.PATCH` (ejemplo: `0.1.5`):

1.  **MAJOR (Mayor)**: Cambios incompatibles con versiones anteriores.
2.  **MINOR (Menor)**: Nuevas funcionalidades compatibles con versiones anteriores.
3.  **PATCH (Parche)**: Corrección de errores (bugs) compatibles con versiones anteriores.

## Proceso de Lanzamiento (Release)

Para generar una nueva versión en AgrowthApp, sigue estos pasos:

### 1. Actualizar `setup.py` y `__init__.py`
Modifica la versión en `setup.py` y en el archivo `__init__.py` de la carpeta del paquete.

### 2. Comitear y Empujar
```bash
git add .
git commit -m "chore: bump version to X.Y.Z"
git push org main
```

### 3. Crear una Etiqueta (Tag) en Git
```bash
git tag -a vX.Y.Z -m "Release version X.Y.Z"
git push org vX.Y.Z
```
