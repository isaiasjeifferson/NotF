"""Gera o arquivo de versão do Windows (propriedades do .exe) a partir de notara/__init__.py."""
import re
from pathlib import Path

v = re.search(r'"([\d.]+)"', Path("notara/__init__.py").read_text()).group(1)
t = tuple(int(x) for x in v.split(".")) + (0,) * (4 - len(v.split(".")))
Path("installer/versao.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}),
  kids=[StringFileInfo([StringTable('041604B0', [
    StringStruct('CompanyName', 'NotF'), StringStruct('FileDescription', 'NotF - Emissor de NFS-e'),
    StringStruct('FileVersion', '{v}'), StringStruct('ProductName', 'NotF'), StringStruct('ProductVersion', '{v}')])]),
    VarFileInfo([VarStruct('Translation', [1046, 1200])])])
""", encoding="utf-8")
print(v)
