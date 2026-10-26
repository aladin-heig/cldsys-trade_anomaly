// DNEXT's scope: the ten exporters and seven HS4 products of the Comtrade extract.
export const EXPORTERS = ["ARG", "AUS", "BRA", "CAN", "FRA", "KAZ", "ROU", "RUS", "UKR", "USA"] as const;

export const PRODUCTS: { code: string; name: string }[] = [
  { code: "1001", name: "Blé" },
  { code: "1003", name: "Orge" },
  { code: "1005", name: "Maïs" },
  { code: "1201", name: "Soja" },
  { code: "1205", name: "Colza" },
  { code: "1206", name: "Graines de tournesol" },
  { code: "2304", name: "Tourteaux de soja" },
];

export const FREQUENCIES: { code: Frequency; name: string }[] = [
  { code: "Y", name: "Annuelle" },
  { code: "Q", name: "Trimestrielle" },
  { code: "M", name: "Mensuelle" },
];

export type Frequency = "M" | "Q" | "Y";
