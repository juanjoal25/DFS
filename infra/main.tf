terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Usa la VPC y subred por defecto de la cuenta de AWS Academy.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

locals {
  subnet_id = tolist(data.aws_subnets.default.ids)[0]
}

# --- Security groups ---
resource "aws_security_group" "namenode" {
  name        = "dfs-namenode-sg"
  description = "NameNode DFS"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "API NameNode"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "datanode" {
  name        = "dfs-datanode-sg"
  description = "DataNode DFS"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "API DataNode (bloques + pipeline)"
    from_port   = 8100
    to_port     = 8100
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- NameNode ---
resource "aws_instance" "namenode" {
  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = local.subnet_id
  key_name                    = var.key_name
  vpc_security_group_ids      = [aws_security_group.namenode.id]
  associate_public_ip_address = true

  user_data = templatefile("${path.module}/user-data-namenode.sh.tpl", {
    git_repo      = var.git_repo
    block_size_mb = var.block_size_mb
    seed_users    = var.seed_users
  })

  tags = { Name = "dfs-namenode" }
}

# --- DataNodes (3) ---
resource "aws_instance" "datanode" {
  count                       = 3
  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = local.subnet_id
  key_name                    = var.key_name
  vpc_security_group_ids      = [aws_security_group.datanode.id]
  associate_public_ip_address = true

  user_data = templatefile("${path.module}/user-data-datanode.sh.tpl", {
    git_repo     = var.git_repo
    datanode_id  = "datanode${count.index + 1}"
    namenode_ip  = aws_instance.namenode.public_ip
  })

  tags = { Name = "dfs-datanode${count.index + 1}" }

  depends_on = [aws_instance.namenode]
}

output "namenode_url" {
  value = "http://${aws_instance.namenode.public_ip}:8000"
}

output "datanode_urls" {
  value = [for i in aws_instance.datanode : "http://${i.public_ip}:8100"]
}
