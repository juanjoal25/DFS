variable "aws_region" {
  description = "Región de AWS Academy (us-east-1 suele ser la habilitada)."
  type        = string
  default     = "us-east-1"
}

variable "ami_id" {
  description = "AMI de Ubuntu 22.04 LTS en la región elegida."
  type        = string
  # us-east-1 Ubuntu 22.04 (verificar/actualizar antes de aplicar).
  default     = "ami-0e2c8caa4b6378d8c"
}

variable "instance_type" {
  description = "Tipo de instancia (AWS Academy permite t2.micro)."
  type        = string
  default     = "t2.micro"
}

variable "key_name" {
  description = "Nombre del key pair existente en AWS Academy para SSH."
  type        = string
}

variable "git_repo" {
  description = "URL del repositorio Git con el código del DFS."
  type        = string
}

variable "block_size_mb" {
  description = "Tamaño de bloque por defecto."
  type        = number
  default     = 64
}

variable "seed_users" {
  description = "Usuarios semilla user:pass separados por coma."
  type        = string
  default     = "admin:admin"
}
