// Phase 1 placeholder. Replaced by azd-generated Bicep when infra lands.
// Resources planned: ACR, Container App (frontend), Foundry hosted-agent, Key Vault,
// Log Analytics + App Insights, managed identities.
targetScope = 'resourceGroup'

@description('Environment name (azd)')
param environmentName string

@description('Location for all resources')
param location string = resourceGroup().location

output environment string = environmentName
output location string = location
