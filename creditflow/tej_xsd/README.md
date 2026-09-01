# Official TEJ RS schemas

Source archive: `plateforme-TEJ-shemas-xsd.zip` from the Tunisia Ministry/JIBAYA TEJ publication.

The distributed `TEJISOPaysDevises.xsd` contains one unmatched closing `</xs:enumeration>` immediately before the `INR` entry (upstream line 1580). The bundled copy removes only that invalid closing tag so XML Schema parsers can compile the official schema set. No type, enumeration, hierarchy, or constraint is altered.

Upstream SHA-256 values before this syntax repair:

- `TEJDeclarationRS_v1.0.xsd`: `cab627ee31e2ffbb75fa36414f8639550790376817af0eca5f52087ddddbfb5f`
- `TEJRSCodesOperations_v1.0.xsd`: `43f5f35c8e5a19d3db9fe048d30fc17f9fc2751eac0ec8c718eee9635123fe15`
- `TEJISOPaysDevises.xsd`: `7ea55379c680f3ccb58560049ca13c4aca52a563f241ef3478ec6c97020df407`
