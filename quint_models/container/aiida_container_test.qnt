module aiida_container_test {
  import aiida_container.* from "./aiida_container"

  val imageBuilt: bool =
    state.image == Valid

  val dataLockAcquired: bool =
    state.lockHeld

  val servicesStarted: bool =
    state.postgres == Starting and state.rabbitmq == Starting

  val bundledPostgresAccepted: bool =
    state.postgres == Ready and state.postgresIdentity == BundledPostgres

  val rabbitAccepted: bool =
    state.rabbitmq == Ready

  val aiidaBecameReady: bool =
    state.session == RunningCommand and state.aiidaDaemon == Ready

  val successfulShutdownFinished: bool =
    and {
      state.session == Finished,
      state.commandExited,
      state.failure == NoFailure,
    }

  val detachedSessionStopped: bool =
    and {
      state.mode == Detached,
      state.session == Finished,
      state.stopRequested,
      state.failure == NoFailure,
    }

  val attachedShellRan: bool =
    state.attachedShells > 0 and state.aiidaDaemon == Ready

  val severalShellsAttached: bool =
    state.attachedShells > 1

  // Nothing stops an attached shell from outliving the session it was using;
  // once that happens it has no services left to talk to.
  val attachedShellOutlivedSession: bool =
    state.attachedShells > 0 and state.session == Finished

  val failedServiceStartupWasCleanedUp: bool =
    and {
      state.session == Finished,
      state.failure != NoFailure,
    }

  val missingImageRejected: bool =
    state.failure == MissingImage

  val badImagePatchRejected: bool =
    state.failure == PatchRejected

  val missingApptainerRejected: bool =
    state.failure == MissingApptainer

  val busyDataDirectoryRejected: bool =
    state.failure == DataDirectoryBusy

  val unavailablePostgresRejected: bool =
    state.failure == PostgresUnavailable

  val foreignPostgresRejected: bool =
    state.failure == ForeignPostgresDetected

  val unavailableRabbitRejected: bool =
    state.failure == RabbitUnavailable
}
