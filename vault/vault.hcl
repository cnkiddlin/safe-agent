ui = true

listener "tcp" {
  address     = "127.0.0.1:8200"
  tls_disable = 1
}

storage "postgresql" {
  connection_url = "postgres://vault:password@localhost:5432/vault?sslmode=disable"
  table          = "vault_kv_store"
}

api_addr = "http://vault:8200"
