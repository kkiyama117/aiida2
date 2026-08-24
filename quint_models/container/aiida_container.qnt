// Lifecycle model for run.sh and containerfiles/aiida.def.
//
// Covers image patch validation, the single-data-directory lock, dependency
// readiness, the foreground command lifetime, and supervised shutdown.
// Abstracts shell details, s6 internals, ports, time, and application traffic.
// Source map: this module corresponds to both files named above. Update and
// rerun it whenever either source file changes.
module aiida_container {
  type ImagePhase = Absent | Building | Valid | Invalid
  type SessionPhase =
    | Idle
    | CheckingFlock
    | AcquiringLock
    | Launching
    | StartingServices
    | RunningCommand
    | Stopping
    | Finished
    | Failed
  type ServicePhase = Down | Starting | Ready
  type PostgresIdentity = UnknownPostgres | BundledPostgres | ForeignPostgres
  type FailureReason =
    | NoFailure
    | MissingImage
    | PatchRejected
    | MissingFlock
    | DataDirectoryBusy
    | PostgresUnavailable
    | ForeignPostgresDetected
    | RabbitUnavailable

  type ContainerState = {
    image: ImagePhase,
    session: SessionPhase,
    lockHeld: bool,
    postgres: ServicePhase,
    postgresIdentity: PostgresIdentity,
    rabbitmq: ServicePhase,
    aiidaDaemon: ServicePhase,
    profileReady: bool,
    commandExited: bool,
    failure: FailureReason,
  }

  var state: ContainerState

  pure val initialState: ContainerState = {
    image: Absent,
    session: Idle,
    lockHeld: false,
    postgres: Down,
    postgresIdentity: UnknownPostgres,
    rabbitmq: Down,
    aiidaDaemon: Down,
    profileReady: false,
    commandExited: false,
    failure: NoFailure,
  }

  pure def canBeginBuild(s: ContainerState): bool =
    s.image == Absent and s.session == Idle

  pure def applyBeginBuild(s: ContainerState): ContainerState =
    { ...s, image: Building }

  pure def canFinishBuild(s: ContainerState): bool =
    s.image == Building and s.session == Idle

  pure def applyBuildSuccess(s: ContainerState): ContainerState =
    { ...s, image: Valid }

  pure def applyBuildRejected(s: ContainerState): ContainerState =
    { ...s, image: Invalid, session: Failed, failure: PatchRejected }

  pure def canStartSession(s: ContainerState): bool =
    s.image == Valid and s.session == Idle

  pure def applyStartSession(s: ContainerState): ContainerState =
    { ...s, session: CheckingFlock }

  pure def canRejectMissingImage(s: ContainerState): bool =
    s.image == Absent and s.session == Idle

  pure def applyMissingImage(s: ContainerState): ContainerState =
    { ...s, session: Failed, failure: MissingImage }

  pure def canCheckFlock(s: ContainerState): bool =
    s.session == CheckingFlock

  pure def applyFlockPresent(s: ContainerState): ContainerState =
    { ...s, session: AcquiringLock }

  pure def applyMissingFlock(s: ContainerState): ContainerState =
    { ...s, session: Failed, failure: MissingFlock }

  pure def canAcquireLock(s: ContainerState): bool =
    s.session == AcquiringLock

  pure def applyLockAcquired(s: ContainerState): ContainerState =
    { ...s, session: Launching, lockHeld: true }

  pure def applyLockBusy(s: ContainerState): ContainerState =
    { ...s, session: Failed, failure: DataDirectoryBusy }

  pure def canLaunch(s: ContainerState): bool =
    s.session == Launching and s.lockHeld

  pure def applyLaunch(s: ContainerState): ContainerState =
    {
      ...s,
      session: StartingServices,
      postgres: Starting,
      rabbitmq: Starting,
    }

  pure def canProbePostgres(s: ContainerState): bool =
    s.session == StartingServices and s.postgres == Starting

  pure def applyBundledPostgresReady(s: ContainerState): ContainerState =
    { ...s, postgres: Ready, postgresIdentity: BundledPostgres }

  pure def applyPostgresFailure(
    s: ContainerState,
    identity: PostgresIdentity,
    reason: FailureReason,
  ): ContainerState =
    {
      ...s,
      session: Stopping,
      postgresIdentity: identity,
      failure: reason,
    }

  pure def canProbeRabbit(s: ContainerState): bool =
    s.session == StartingServices and s.rabbitmq == Starting

  pure def applyRabbitReady(s: ContainerState): ContainerState =
    { ...s, rabbitmq: Ready }

  pure def applyRabbitFailure(s: ContainerState): ContainerState =
    { ...s, session: Stopping, failure: RabbitUnavailable }

  pure def canStartAiida(s: ContainerState): bool =
    and {
      s.session == StartingServices,
      s.postgres == Ready,
      s.postgresIdentity == BundledPostgres,
      s.rabbitmq == Ready,
      s.aiidaDaemon == Down,
    }

  pure def applyStartAiida(s: ContainerState): ContainerState =
    { ...s, aiidaDaemon: Starting }

  pure def canFinishAiidaStart(s: ContainerState): bool =
    and {
      s.session == StartingServices,
      s.postgres == Ready,
      s.postgresIdentity == BundledPostgres,
      s.rabbitmq == Ready,
      s.aiidaDaemon == Starting,
    }

  pure def applyAiidaReady(s: ContainerState): ContainerState =
    {
      ...s,
      session: RunningCommand,
      aiidaDaemon: Ready,
      profileReady: true,
    }

  pure def canExitCommand(s: ContainerState): bool =
    s.session == RunningCommand and s.aiidaDaemon == Ready

  pure def applyCommandExit(s: ContainerState): ContainerState =
    { ...s, session: Stopping, commandExited: true }

  pure def canStopAiida(s: ContainerState): bool =
    s.session == Stopping and s.aiidaDaemon != Down

  pure def applyStopAiida(s: ContainerState): ContainerState =
    { ...s, aiidaDaemon: Down }

  pure def canStopRabbit(s: ContainerState): bool =
    and {
      s.session == Stopping,
      s.aiidaDaemon == Down,
      s.rabbitmq != Down,
    }

  pure def applyStopRabbit(s: ContainerState): ContainerState =
    { ...s, rabbitmq: Down }

  pure def canStopPostgres(s: ContainerState): bool =
    and {
      s.session == Stopping,
      s.aiidaDaemon == Down,
      s.postgres != Down,
    }

  pure def applyStopPostgres(s: ContainerState): ContainerState =
    { ...s, postgres: Down }

  pure def canFinishShutdown(s: ContainerState): bool =
    and {
      s.session == Stopping,
      s.lockHeld,
      s.aiidaDaemon == Down,
      s.rabbitmq == Down,
      s.postgres == Down,
    }

  pure def applyFinishShutdown(s: ContainerState): ContainerState =
    { ...s, session: Finished, lockHeld: false }

  action init: bool =
    state' = initialState

  action beginBuild: bool = all {
    canBeginBuild(state),
    state' = applyBeginBuild(state),
  }

  action buildSuccess: bool = all {
    canFinishBuild(state),
    state' = applyBuildSuccess(state),
  }

  action buildRejected: bool = all {
    canFinishBuild(state),
    state' = applyBuildRejected(state),
  }

  action startSession: bool = all {
    canStartSession(state),
    state' = applyStartSession(state),
  }

  action rejectMissingImage: bool = all {
    canRejectMissingImage(state),
    state' = applyMissingImage(state),
  }

  action flockPresent: bool = all {
    canCheckFlock(state),
    state' = applyFlockPresent(state),
  }

  action rejectMissingFlock: bool = all {
    canCheckFlock(state),
    state' = applyMissingFlock(state),
  }

  action acquireDataLock: bool = all {
    canAcquireLock(state),
    state' = applyLockAcquired(state),
  }

  action rejectBusyDataDirectory: bool = all {
    canAcquireLock(state),
    state' = applyLockBusy(state),
  }

  action launchContainer: bool = all {
    canLaunch(state),
    state' = applyLaunch(state),
  }

  action bundledPostgresReady: bool = all {
    canProbePostgres(state),
    state' = applyBundledPostgresReady(state),
  }

  action rejectForeignPostgres: bool = all {
    canProbePostgres(state),
    state' = applyPostgresFailure(
      state,
      ForeignPostgres,
      ForeignPostgresDetected,
    ),
  }

  action postgresUnavailable: bool = all {
    canProbePostgres(state),
    state' = applyPostgresFailure(
      state,
      UnknownPostgres,
      PostgresUnavailable,
    ),
  }

  action rabbitReady: bool = all {
    canProbeRabbit(state),
    state' = applyRabbitReady(state),
  }

  action rabbitUnavailable: bool = all {
    canProbeRabbit(state),
    state' = applyRabbitFailure(state),
  }

  action startAiida: bool = all {
    canStartAiida(state),
    state' = applyStartAiida(state),
  }

  action aiidaReady: bool = all {
    canFinishAiidaStart(state),
    state' = applyAiidaReady(state),
  }

  action commandExit: bool = all {
    canExitCommand(state),
    state' = applyCommandExit(state),
  }

  action stopAiida: bool = all {
    canStopAiida(state),
    state' = applyStopAiida(state),
  }

  action stopRabbit: bool = all {
    canStopRabbit(state),
    state' = applyStopRabbit(state),
  }

  action stopPostgres: bool = all {
    canStopPostgres(state),
    state' = applyStopPostgres(state),
  }

  action finishShutdown: bool = all {
    canFinishShutdown(state),
    state' = applyFinishShutdown(state),
  }

  action step: bool = any {
    beginBuild,
    buildSuccess,
    buildRejected,
    startSession,
    rejectMissingImage,
    flockPresent,
    rejectMissingFlock,
    acquireDataLock,
    rejectBusyDataDirectory,
    launchContainer,
    bundledPostgresReady,
    rejectForeignPostgres,
    postgresUnavailable,
    rabbitReady,
    rabbitUnavailable,
    startAiida,
    aiidaReady,
    commandExit,
    stopAiida,
    stopRabbit,
    stopPostgres,
    finishShutdown,
  }

  val aiidaRequiresReadyDependencies: bool =
    state.aiidaDaemon != Down implies and {
      state.postgres == Ready,
      state.postgresIdentity == BundledPostgres,
      state.rabbitmq == Ready,
    }

  val profileUsesBundledServices: bool =
    state.profileReady implies and {
      state.postgresIdentity == BundledPostgres,
      state.rabbitmq == Ready or state.session == Stopping or state.session == Finished,
    }

  val servicesRequireDataLock: bool =
    or {
      and {
        state.postgres == Down,
        state.rabbitmq == Down,
        state.aiidaDaemon == Down,
      },
      state.lockHeld,
    }

  val foreignPostgresIsRefused: bool =
    state.postgresIdentity == ForeignPostgres implies and {
      state.aiidaDaemon == Down,
      not(state.profileReady),
      state.failure == ForeignPostgresDetected,
    }

  val finishedSessionIsClean: bool =
    state.session == Finished implies and {
      not(state.lockHeld),
      state.postgres == Down,
      state.rabbitmq == Down,
      state.aiidaDaemon == Down,
    }
}
