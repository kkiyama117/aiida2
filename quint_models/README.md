# Quint models

Models are classified by which side of AiiDA they describe:

- [`container/`](container/): container build, startup, PostgreSQL/RabbitMQ
  readiness, AiiDA daemon lifecycle, and shutdown.
- [`calc_flow/`](calc_flow/): scientific calculation workflows, such as viscosity
  calculations using DFT or molecular dynamics.
