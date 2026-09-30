{
  "schema_version": "0.1",
  "phase": "7l",
  "note": "Optics vertical slice",
  "nodes": [
    {
      "id": "OP:fo:snell",
      "labels": [
        "Entity",
        "Formula"
      ],
      "props": {
        "name": "Snell's law",
        "ntype": "formula",
        "domain": "physics.optics",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "latex": "n_1\\sin\\theta_1=n_2\\sin\\theta_2"
      }
    },
    {
      "id": "OP:fo:lens",
      "labels": [
        "Entity",
        "Formula"
      ],
      "props": {
        "name": "Thin lens equation",
        "ntype": "formula",
        "domain": "physics.optics",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "latex": "\\frac{1}{f}=\\frac{1}{u}+\\frac{1}{v}"
      }
    },
    {
      "id": "OP:fo:mirror",
      "labels": [
        "Entity",
        "Formula"
      ],
      "props": {
        "name": "Mirror equation",
        "ntype": "formula",
        "domain": "physics.optics",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "latex": "\\frac{1}{f}=\\frac{1}{d_o}+\\frac{1}{d_i}"
      }
    },
    {
      "id": "OP:fo:refr_speed",
      "labels": [
        "Entity",
        "Formula"
      ],
      "props": {
        "name": "Speed of light in medium",
        "ntype": "formula",
        "domain": "physics.optics",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "latex": "v=\\frac{c}{n}"
      }
    },
    {
      "id": "OP:pq:refractive_index",
      "labels": [
        "Entity",
        "PhysicalQuantity"
      ],
      "props": {
        "name": "Refractive index",
        "ntype": "physical_quantity",
        "domain": "phys.quantity",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "symbol": "n",
        "dimension": "dimensionless"
      }
    },
    {
      "id": "OP:pq:focal_length",
      "labels": [
        "Entity",
        "PhysicalQuantity"
      ],
      "props": {
        "name": "Focal length",
        "ntype": "physical_quantity",
        "domain": "phys.quantity",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "symbol": "f",
        "dimension": "L"
      }
    },
    {
      "id": "OP:pq:object_dist",
      "labels": [
        "Entity",
        "PhysicalQuantity"
      ],
      "props": {
        "name": "Object distance",
        "ntype": "physical_quantity",
        "domain": "phys.quantity",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "symbol": "u",
        "dimension": "L"
      }
    },
    {
      "id": "OP:pq:image_dist",
      "labels": [
        "Entity",
        "PhysicalQuantity"
      ],
      "props": {
        "name": "Image distance",
        "ntype": "physical_quantity",
        "domain": "phys.quantity",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "symbol": "v",
        "dimension": "L"
      }
    },
    {
      "id": "OP:un:meter",
      "labels": [
        "Entity",
        "Unit"
      ],
      "props": {
        "name": "meter",
        "ntype": "unit",
        "domain": "phys.unit",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "symbol": "m",
        "dimension": "L",
        "si_base": true
      }
    },
    {
      "id": "OP:sy:n",
      "labels": [
        "Entity",
        "Symbol"
      ],
      "props": {
        "name": "n (refractive index)",
        "ntype": "symbol",
        "domain": "math.symbol",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "latex": "n"
      }
    },
    {
      "id": "OP:sy:f",
      "labels": [
        "Entity",
        "Symbol"
      ],
      "props": {
        "name": "f (focal length)",
        "ntype": "symbol",
        "domain": "math.symbol",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "latex": "f"
      }
    },
    {
      "id": "OP:sy:u",
      "labels": [
        "Entity",
        "Symbol"
      ],
      "props": {
        "name": "u (object distance)",
        "ntype": "symbol",
        "domain": "math.symbol",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "latex": "u"
      }
    },
    {
      "id": "OP:sy:v",
      "labels": [
        "Entity",
        "Symbol"
      ],
      "props": {
        "name": "v (image distance)",
        "ntype": "symbol",
        "domain": "math.symbol",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "latex": "v"
      }
    },
    {
      "id": "OP:sy:theta",
      "labels": [
        "Entity",
        "Symbol"
      ],
      "props": {
        "name": "θ (angle)",
        "ntype": "symbol",
        "domain": "math.symbol",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "latex": "\\theta"
      }
    },
    {
      "id": "OP:sy:c",
      "labels": [
        "Entity",
        "Symbol"
      ],
      "props": {
        "name": "c (speed of light)",
        "ntype": "symbol",
        "domain": "math.symbol",
        "source": "curated_seed",
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "created_at": "2026-09-30T08:33:31.138283",
        "latex": "c"
      }
    }
  ],
  "edges": [
    {
      "id": "OP:fo:snell->OP:sy:n[has_symbol]",
      "source": "OP:fo:snell",
      "target": "OP:sy:n",
      "type": "has_symbol",
      "kind": "formula_symbol",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_symbol",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:snell->OP:sy:theta[has_symbol]",
      "source": "OP:fo:snell",
      "target": "OP:sy:theta",
      "type": "has_symbol",
      "kind": "formula_symbol",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_symbol",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:lens->OP:sy:f[has_symbol]",
      "source": "OP:fo:lens",
      "target": "OP:sy:f",
      "type": "has_symbol",
      "kind": "formula_symbol",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_symbol",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:lens->OP:sy:u[has_symbol]",
      "source": "OP:fo:lens",
      "target": "OP:sy:u",
      "type": "has_symbol",
      "kind": "formula_symbol",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_symbol",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:lens->OP:sy:v[has_symbol]",
      "source": "OP:fo:lens",
      "target": "OP:sy:v",
      "type": "has_symbol",
      "kind": "formula_symbol",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_symbol",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:mirror->OP:sy:f[has_symbol]",
      "source": "OP:fo:mirror",
      "target": "OP:sy:f",
      "type": "has_symbol",
      "kind": "formula_symbol",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_symbol",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:mirror->OP:sy:u[has_symbol]",
      "source": "OP:fo:mirror",
      "target": "OP:sy:u",
      "type": "has_symbol",
      "kind": "formula_symbol",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_symbol",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:mirror->OP:sy:v[has_symbol]",
      "source": "OP:fo:mirror",
      "target": "OP:sy:v",
      "type": "has_symbol",
      "kind": "formula_symbol",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_symbol",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:refr_speed->OP:sy:c[has_symbol]",
      "source": "OP:fo:refr_speed",
      "target": "OP:sy:c",
      "type": "has_symbol",
      "kind": "formula_symbol",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_symbol",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:refr_speed->OP:sy:n[has_symbol]",
      "source": "OP:fo:refr_speed",
      "target": "OP:sy:n",
      "type": "has_symbol",
      "kind": "formula_symbol",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_symbol",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:snell->OP:pq:refractive_index[defines]",
      "source": "OP:fo:snell",
      "target": "OP:pq:refractive_index",
      "type": "defines",
      "kind": "formula_quantity",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_quantity",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:lens->OP:pq:focal_length[defines]",
      "source": "OP:fo:lens",
      "target": "OP:pq:focal_length",
      "type": "defines",
      "kind": "formula_quantity",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_quantity",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:lens->OP:pq:object_dist[defines]",
      "source": "OP:fo:lens",
      "target": "OP:pq:object_dist",
      "type": "defines",
      "kind": "formula_quantity",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_quantity",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:lens->OP:pq:image_dist[defines]",
      "source": "OP:fo:lens",
      "target": "OP:pq:image_dist",
      "type": "defines",
      "kind": "formula_quantity",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_quantity",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:mirror->OP:pq:focal_length[defines]",
      "source": "OP:fo:mirror",
      "target": "OP:pq:focal_length",
      "type": "defines",
      "kind": "formula_quantity",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_quantity",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:mirror->OP:pq:object_dist[defines]",
      "source": "OP:fo:mirror",
      "target": "OP:pq:object_dist",
      "type": "defines",
      "kind": "formula_quantity",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_quantity",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:mirror->OP:pq:image_dist[defines]",
      "source": "OP:fo:mirror",
      "target": "OP:pq:image_dist",
      "type": "defines",
      "kind": "formula_quantity",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_quantity",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:fo:refr_speed->OP:pq:refractive_index[defines]",
      "source": "OP:fo:refr_speed",
      "target": "OP:pq:refractive_index",
      "type": "defines",
      "kind": "formula_quantity",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "formula_quantity",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:pq:focal_length->OP:un:meter[has_unit]",
      "source": "OP:pq:focal_length",
      "target": "OP:un:meter",
      "type": "has_unit",
      "kind": "quantity_unit",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "quantity_unit",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:pq:object_dist->OP:un:meter[has_unit]",
      "source": "OP:pq:object_dist",
      "target": "OP:un:meter",
      "type": "has_unit",
      "kind": "quantity_unit",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "quantity_unit",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:pq:image_dist->OP:un:meter[has_unit]",
      "source": "OP:pq:image_dist",
      "target": "OP:un:meter",
      "type": "has_unit",
      "kind": "quantity_unit",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "quantity_unit",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    },
    {
      "id": "OP:un:meter->UN:m[same_as]",
      "source": "OP:un:meter",
      "target": "UN:m",
      "type": "same_as",
      "kind": "seed_to_existing",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "seed_to_existing",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283",
        "alignment": "manual_curation"
      }
    },
    {
      "id": "OP:sy:c->RT:sy:c[same_as]",
      "source": "OP:sy:c",
      "target": "RT:sy:c",
      "type": "same_as",
      "kind": "seed_to_existing",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "seed_to_existing",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283",
        "alignment": "manual_curation"
      }
    },
    {
      "id": "OP:fo:refr_speed->RT:pq:light_speed[derived_from]",
      "source": "OP:fo:refr_speed",
      "target": "RT:pq:light_speed",
      "type": "derived_from",
      "kind": "speed_in_medium",
      "props": {
        "confidence": 1.0,
        "explicit_or_inferred": "explicit",
        "kind": "speed_in_medium",
        "source": "curated_seed",
        "created_at": "2026-09-30T08:33:31.138283"
      }
    }
  ]
}