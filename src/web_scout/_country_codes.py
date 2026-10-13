"""Dependency-free ISO 3166-1 country-name resolution."""

import re
import unicodedata

# Common English names paired with ISO 3166-1 alpha-2 codes. The list is based
# on the public-domain ISO table distributed with IANA tzdata.
_ISO_COUNTRIES = """\
AD\tAndorra
AE\tUnited Arab Emirates
AF\tAfghanistan
AG\tAntigua and Barbuda
AI\tAnguilla
AL\tAlbania
AM\tArmenia
AO\tAngola
AQ\tAntarctica
AR\tArgentina
AS\tAmerican Samoa
AT\tAustria
AU\tAustralia
AW\tAruba
AX\tAland Islands
AZ\tAzerbaijan
BA\tBosnia and Herzegovina
BB\tBarbados
BD\tBangladesh
BE\tBelgium
BF\tBurkina Faso
BG\tBulgaria
BH\tBahrain
BI\tBurundi
BJ\tBenin
BL\tSaint Barthelemy
BM\tBermuda
BN\tBrunei
BO\tBolivia
BQ\tBonaire Sint Eustatius and Saba
BR\tBrazil
BS\tBahamas
BT\tBhutan
BV\tBouvet Island
BW\tBotswana
BY\tBelarus
BZ\tBelize
CA\tCanada
CC\tCocos Keeling Islands
CD\tDemocratic Republic of the Congo
CF\tCentral African Republic
CG\tRepublic of the Congo
CH\tSwitzerland
CI\tCote d'Ivoire
CK\tCook Islands
CL\tChile
CM\tCameroon
CN\tChina
CO\tColombia
CR\tCosta Rica
CU\tCuba
CV\tCabo Verde
CW\tCuracao
CX\tChristmas Island
CY\tCyprus
CZ\tCzechia
DE\tGermany
DJ\tDjibouti
DK\tDenmark
DM\tDominica
DO\tDominican Republic
DZ\tAlgeria
EC\tEcuador
EE\tEstonia
EG\tEgypt
EH\tWestern Sahara
ER\tEritrea
ES\tSpain
ET\tEthiopia
FI\tFinland
FJ\tFiji
FK\tFalkland Islands
FM\tMicronesia
FO\tFaroe Islands
FR\tFrance
GA\tGabon
GB\tUnited Kingdom
GD\tGrenada
GE\tGeorgia
GF\tFrench Guiana
GG\tGuernsey
GH\tGhana
GI\tGibraltar
GL\tGreenland
GM\tGambia
GN\tGuinea
GP\tGuadeloupe
GQ\tEquatorial Guinea
GR\tGreece
GS\tSouth Georgia and the South Sandwich Islands
GT\tGuatemala
GU\tGuam
GW\tGuinea-Bissau
GY\tGuyana
HK\tHong Kong
HM\tHeard Island and McDonald Islands
HN\tHonduras
HR\tCroatia
HT\tHaiti
HU\tHungary
ID\tIndonesia
IE\tIreland
IL\tIsrael
IM\tIsle of Man
IN\tIndia
IO\tBritish Indian Ocean Territory
IQ\tIraq
IR\tIran
IS\tIceland
IT\tItaly
JE\tJersey
JM\tJamaica
JO\tJordan
JP\tJapan
KE\tKenya
KG\tKyrgyzstan
KH\tCambodia
KI\tKiribati
KM\tComoros
KN\tSaint Kitts and Nevis
KP\tNorth Korea
KR\tSouth Korea
KW\tKuwait
KY\tCayman Islands
KZ\tKazakhstan
LA\tLaos
LB\tLebanon
LC\tSaint Lucia
LI\tLiechtenstein
LK\tSri Lanka
LR\tLiberia
LS\tLesotho
LT\tLithuania
LU\tLuxembourg
LV\tLatvia
LY\tLibya
MA\tMorocco
MC\tMonaco
MD\tMoldova
ME\tMontenegro
MF\tSaint Martin
MG\tMadagascar
MH\tMarshall Islands
MK\tNorth Macedonia
ML\tMali
MM\tMyanmar
MN\tMongolia
MO\tMacao
MP\tNorthern Mariana Islands
MQ\tMartinique
MR\tMauritania
MS\tMontserrat
MT\tMalta
MU\tMauritius
MV\tMaldives
MW\tMalawi
MX\tMexico
MY\tMalaysia
MZ\tMozambique
NA\tNamibia
NC\tNew Caledonia
NE\tNiger
NF\tNorfolk Island
NG\tNigeria
NI\tNicaragua
NL\tNetherlands
NO\tNorway
NP\tNepal
NR\tNauru
NU\tNiue
NZ\tNew Zealand
OM\tOman
PA\tPanama
PE\tPeru
PF\tFrench Polynesia
PG\tPapua New Guinea
PH\tPhilippines
PK\tPakistan
PL\tPoland
PM\tSaint Pierre and Miquelon
PN\tPitcairn
PR\tPuerto Rico
PS\tPalestine
PT\tPortugal
PW\tPalau
PY\tParaguay
QA\tQatar
RE\tReunion
RO\tRomania
RS\tSerbia
RU\tRussia
RW\tRwanda
SA\tSaudi Arabia
SB\tSolomon Islands
SC\tSeychelles
SD\tSudan
SE\tSweden
SG\tSingapore
SH\tSaint Helena
SI\tSlovenia
SJ\tSvalbard and Jan Mayen
SK\tSlovakia
SL\tSierra Leone
SM\tSan Marino
SN\tSenegal
SO\tSomalia
SR\tSuriname
SS\tSouth Sudan
ST\tSao Tome and Principe
SV\tEl Salvador
SX\tSint Maarten
SY\tSyria
SZ\tEswatini
TC\tTurks and Caicos Islands
TD\tChad
TF\tFrench Southern Territories
TG\tTogo
TH\tThailand
TJ\tTajikistan
TK\tTokelau
TL\tTimor-Leste
TM\tTurkmenistan
TN\tTunisia
TO\tTonga
TR\tTurkiye
TT\tTrinidad and Tobago
TV\tTuvalu
TW\tTaiwan
TZ\tTanzania
UA\tUkraine
UG\tUganda
UM\tUnited States Minor Outlying Islands
US\tUnited States
UY\tUruguay
UZ\tUzbekistan
VA\tVatican City
VC\tSaint Vincent and the Grenadines
VE\tVenezuela
VG\tBritish Virgin Islands
VI\tUnited States Virgin Islands
VN\tVietnam
VU\tVanuatu
WF\tWallis and Futuna
WS\tSamoa
YE\tYemen
YT\tMayotte
ZA\tSouth Africa
ZM\tZambia
ZW\tZimbabwe
"""


def _normalize_country_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    words = re.sub(r"[^a-z0-9]+", " ", ascii_text.casefold()).strip().split()
    if words[:1] == ["the"]:
        words = words[1:]
    return " ".join(words)


_COUNTRY_CODES_BY_NAME = {
    _normalize_country_name(name): code.lower()
    for line in _ISO_COUNTRIES.splitlines()
    for code, name in [line.split("\t", 1)]
}

_COUNTRY_CODES_BY_NAME.update(
    {
        _normalize_country_name(alias): code.lower()
        for alias, code in {
            "Bahamas, The": "BS",
            "Bolivia, Plurinational State of": "BO",
            "Bosnia & Herzegovina": "BA",
            "Brunei Darussalam": "BN",
            "Burma": "MM",
            "Cape Verde": "CV",
            "Congo-Brazzaville": "CG",
            "Congo-Kinshasa": "CD",
            "Democratic People's Republic of Korea": "KP",
            "DR Congo": "CD",
            "DRC": "CD",
            "East Timor": "TL",
            "Federated States of Micronesia": "FM",
            "Gambia, The": "GM",
            "Great Britain": "GB",
            "Holy See": "VA",
            "Iran, Islamic Republic of": "IR",
            "Ivory Coast": "CI",
            "Lao People's Democratic Republic": "LA",
            "Macao SAR": "MO",
            "Macau": "MO",
            "Moldova, Republic of": "MD",
            "Palestinian Territories": "PS",
            "Republic of Korea": "KR",
            "Russian Federation": "RU",
            "State of Palestine": "PS",
            "Swaziland": "SZ",
            "Syrian Arab Republic": "SY",
            "Turkey": "TR",
            "UK": "GB",
            "United Republic of Tanzania": "TZ",
            "United States of America": "US",
            "USA": "US",
            "Venezuela, Bolivarian Republic of": "VE",
            "Viet Nam": "VN",
        }.items()
    }
)


def resolve_country_code(country_name: str | None) -> str | None:
    """Return a lowercase ISO alpha-2 code for a recognized country name."""
    if not country_name:
        return None
    return _COUNTRY_CODES_BY_NAME.get(_normalize_country_name(country_name))


__all__ = ["resolve_country_code"]
