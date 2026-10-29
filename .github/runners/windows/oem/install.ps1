# Sets up a Windows runner VM (docs/ci-runners.md): the tools the workflows
# expect from GitHub's hosted Windows image, then the Actions runner as a
# service. The registration token arrives later in the VM's shared folder
# (\\host.lan\Data\runner.token), dropped by register-windows.sh, because
# a token expires an hour after it is minted. The account's password comes
# the same way and is deleted once the service is set up.
$ErrorActionPreference = 'Stop'
$RunnerVersion = '2.338.2'
$Shared = '\\host.lan\Data'
$Runner = 'C:\actions-runner'

Set-ExecutionPolicy Bypass -Scope Process -Force
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    Invoke-Expression ((New-Object Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
    $env:Path += ';C:\ProgramData\chocolatey\bin'
}
# Git Bash (shell: bash), PowerShell 7 (shell: pwsh), Inno Setup
# (package-windows), MSVC and CMake (voice engine builds).
choco install -y --no-progress git powershell-core 7zip innosetup cmake jq
choco install -y --no-progress visualstudio2022buildtools `
    --package-parameters '--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --passive'
# Long paths: node_modules and Electron's output nest deeply.
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' LongPathsEnabled 1
# Defender scanning every extracted wheel and node module triples build times;
# the VM only runs this repository's jobs.
Add-MpPreference -ExclusionPath $Runner, 'C:\hostedtoolcache'

New-Item -ItemType Directory -Force $Runner | Out-Null
$zip = "$env:TEMP\actions-runner.zip"
Invoke-WebRequest "https://github.com/actions/runner/releases/download/v$RunnerVersion/actions-runner-win-x64-$RunnerVersion.zip" -OutFile $zip
Expand-Archive $zip -DestinationPath $Runner -Force

Write-Output "Waiting for $Shared\runner.token (run .github/runners/windows/register-windows.sh on the host)"
while (-not (Test-Path "$Shared\runner.token")) { Start-Sleep 15 }
$token = (Get-Content "$Shared\runner.token" -Raw).Trim()
$name = (Get-Content "$Shared\runner-name" -Raw).Trim()
$url = (Get-Content "$Shared\runner-url" -Raw).Trim()
$user = (Get-Content "$Shared\runner-user" -Raw).Trim()
$password = (Get-Content "$Shared\runner-password" -Raw).Trim()
Remove-Item "$Shared\runner.token", "$Shared\runner-password"

# The service runs as the VM's administrator, as hosted runners do, so jobs
# can install and uninstall the Windows package they build.
& "$Runner\config.cmd" --unattended --url $url --token $token --name $name `
    --labels imprint-windows --work _work --replace `
    --runasservice --windowslogonaccount ".\$user" --windowslogonpassword $password
if ($LASTEXITCODE -ne 0) { throw "Runner registration failed with $LASTEXITCODE" }
Write-Output "Runner $name is registered and running"
